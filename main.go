package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	_ "image/gif"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
)

const (
	RED    = "\033[91m"
	WHITE  = "\033[97m"
	GREEN  = "\033[92m"
	YELLOW = "\033[93m"
	BLUE   = "\033[94m"
	CYAN   = "\033[96m"
	BOLD   = "\033[1m"
	RESET  = "\033[0m"
)

type Endpoint struct {
	Kind       string
	ID         string
	Version    string
	ReplayPath string
}

type Endpoints struct {
	Mode   string
	Source Endpoint
	Target Endpoint
}

type RibeiroCLI struct {
	monitoring        bool
	lastDevices       map[string]struct{}
	lastFilesSource   map[string]map[string]struct{}
	sourceVersion     string
	targetVersion     string
	mode              string
	localVersion      string
	localFfrtcNormal  string
	localFfrtcMax     string
	localReplayNormal string
	localReplayMax    string
	sourceReplayPath  string
	targetReplayPath  string
	mu                sync.Mutex
}

func NewRibeiroCLI(targetVersion, sourceVersion, mode, localVersion string) *RibeiroCLI {
	if targetVersion == "" {
		targetVersion = "normal"
	}
	if sourceVersion == "" {
		sourceVersion = "normal"
	}
	if mode == "" {
		mode = "auto"
	}
	if localVersion == "" {
		localVersion = "normal"
	}
	sourceReplay := "/storage/emulated/0/Android/data/com.dts.freefireth/files/MReplays"
	targetReplay := sourceReplay
	if sourceVersion == "max" {
		sourceReplay = "/storage/emulated/0/Android/data/com.dts.freefiremax/files/MReplays"
	}
	if targetVersion == "max" {
		targetReplay = "/storage/emulated/0/Android/data/com.dts.freefiremax/files/MReplays"
	}
	return &RibeiroCLI{
		lastDevices:     map[string]struct{}{},
		lastFilesSource: map[string]map[string]struct{}{},
		sourceVersion:   sourceVersion, targetVersion: targetVersion,
		mode: mode, localVersion: localVersion,
		localFfrtcNormal:  "/sdcard/Android/data/com.dts.freefireth/files/ffrtc_log.txt",
		localFfrtcMax:     "/sdcard/Android/data/com.dts.freefiremax/files/ffrtc_log.txt",
		localReplayNormal: "/sdcard/Android/data/com.dts.freefireth/files/MReplays",
		localReplayMax:    "/sdcard/Android/data/com.dts.freefiremax/files/MReplays",
		sourceReplayPath:  sourceReplay, targetReplayPath: targetReplay,
	}
}

func getADBPath() string {
	if androidHome := os.Getenv("ANDROID_HOME"); androidHome != "" {
		name := "adb"
		if runtime.GOOS == "windows" {
			name = "adb.exe"
		}
		candidate := filepath.Join(androidHome, "platform-tools", name)
		if st, err := os.Stat(candidate); err == nil && !st.IsDir() {
			return candidate
		}
	}
	if p, err := exec.LookPath("adb"); err == nil {
		return p
	}
	return "adb"
}

var adbPath = getADBPath()

func adbEnv() []string { return os.Environ() }

func runCommand(timeout time.Duration, name string, args ...string) (int, string, error) {
	cmd := exec.Command(name, args...)
	cmd.Env = adbEnv()
	var out bytes.Buffer
	cmd.Stdout = &out
	cmd.Stderr = &out
	if timeout <= 0 {
		timeout = 30 * time.Second
	}
	done := make(chan error, 1)
	if err := cmd.Start(); err != nil {
		return 1, "", err
	}
	go func() { done <- cmd.Wait() }()
	select {
	case err := <-done:
		code := 0
		if cmd.ProcessState != nil {
			code = cmd.ProcessState.ExitCode()
		}
		return code, strings.TrimSpace(out.String()), err
	case <-time.After(timeout):
		_ = cmd.Process.Kill()
		return 124, "Tiempo agotado esperando el proceso.", contextDeadlineError{}
	}
}

type contextDeadlineError struct{}

func (contextDeadlineError) Error() string { return "command timeout" }

func isADBAvailable() bool {
	code, _, err := runCommand(10*time.Second, adbPath, "version")
	return err == nil && code == 0
}

func extractTimestamps(statInfo string) map[string]string {
	timestamps := map[string]string{}
	for _, raw := range strings.Split(statInfo, "\n") {
		line := strings.TrimSpace(raw)
		switch {
		case strings.HasPrefix(line, "Access: 2"):
			if i := strings.Index(line, "Access: "); i >= 0 {
				ts := strings.Split(strings.TrimSpace(line[i+len("Access: "):]), " -")[0]
				timestamps["Access"] = strings.TrimSpace(ts)
			}
		case strings.HasPrefix(line, "Modify:"):
			if i := strings.Index(line, "Modify: "); i >= 0 {
				ts := strings.Split(strings.TrimSpace(line[i+len("Modify: "):]), " -")[0]
				timestamps["Modify"] = strings.TrimSpace(ts)
			}
		case strings.HasPrefix(line, "Change:"):
			if i := strings.Index(line, "Change: "); i >= 0 {
				ts := strings.Split(strings.TrimSpace(line[i+len("Change: "):]), " -")[0]
				timestamps["Change"] = strings.TrimSpace(ts)
			}
		}
	}
	return timestamps
}

func adbConnectedSerials(output string) []string {
	seen := map[string]struct{}{}
	var serials []string
	for _, line := range strings.Split(output, "\n") {
		p := strings.Fields(strings.TrimSpace(line))
		if len(p) >= 2 && strings.EqualFold(p[1], "device") {
			if _, ok := seen[p[0]]; !ok {
				seen[p[0]] = struct{}{}
				serials = append(serials, p[0])
			}
		}
	}
	return serials
}

func (c *RibeiroCLI) getADBDevices() []string {
	if !isADBAvailable() {
		return nil
	}
	code, out, err := runCommand(20*time.Second, adbPath, "devices")
	if err != nil || code != 0 {
		return nil
	}
	return adbConnectedSerials(out)
}

func (c *RibeiroCLI) findTargetDevice() string {
	d := c.getADBDevices()
	if len(d) == 0 {
		return ""
	}
	return d[0]
}

func (c *RibeiroCLI) resolveEndpoints(sourceID, targetID, sourceRole string) (Endpoints, error) {
	n := len(c.getADBDevices())
	mode := "adb_adb"
	if c.mode == "mixed" || n == 1 {
		mode = "mixed"
	}
	if mode == "mixed" {
		remote := c.findTargetDevice()
		if remote == "" {
			return Endpoints{}, errors.New("No se detectó ningún dispositivo remoto mediante ADB. Usa `adb devices` para confirmarlo.")
		}
		localReplay := c.localReplayNormal
		if c.localVersion == "max" {
			localReplay = c.localReplayMax
		}
		remoteNormal := "/storage/emulated/0/Android/data/com.dts.freefireth/files/MReplays"
		remoteMax := "/storage/emulated/0/Android/data/com.dts.freefiremax/files/MReplays"
		if sourceRole == "remote_is_source" {
			return Endpoints{Mode: mode, Source: Endpoint{Kind: "adb", ID: remote, Version: c.sourceVersion, ReplayPath: c.sourceReplayPath}, Target: Endpoint{Kind: "local", Version: c.localVersion, ReplayPath: localReplay}}, nil
		}
		rp := remoteNormal
		if c.targetVersion == "max" {
			rp = remoteMax
		}
		return Endpoints{Mode: mode, Source: Endpoint{Kind: "local", Version: c.localVersion, ReplayPath: localReplay}, Target: Endpoint{Kind: "adb", ID: remote, Version: c.targetVersion, ReplayPath: rp}}, nil
	}
	s, t, err := c.selectSourceAndTarget(sourceID, targetID)
	if err != nil {
		return Endpoints{}, err
	}
	return Endpoints{Mode: mode, Source: Endpoint{Kind: "adb", ID: s, Version: c.sourceVersion, ReplayPath: c.sourceReplayPath}, Target: Endpoint{Kind: "adb", ID: t, Version: c.targetVersion, ReplayPath: c.targetReplayPath}}, nil
}

func (c *RibeiroCLI) selectSourceAndTarget(sourceID, targetID string) (string, string, error) {
	devices := c.getADBDevices()
	set := map[string]bool{}
	for _, d := range devices {
		set[d] = true
	}
	if sourceID != "" && targetID != "" {
		if !set[sourceID] {
			return "", "", fmt.Errorf("Dispositivo SOURCE no listado: %s", sourceID)
		}
		if !set[targetID] {
			return "", "", fmt.Errorf("Dispositivo TARGET no listado: %s", targetID)
		}
		return sourceID, targetID, nil
	}
	if len(devices) < 2 {
		return "", "", fmt.Errorf("Se necesitan 2 dispositivos ADB conectados para el envío de celular a celular. Detectados: %d. Indica --source-id y --target-id explícitamente.", len(devices))
	}
	return devices[0], devices[1], nil
}

func shellQuote(s string) string {
	return "'" + strings.ReplaceAll(s, "'", "'\\''") + "'"
}

func (c *RibeiroCLI) fsListBins(ep Endpoint) ([]string, error) {
	if ep.Kind == "local" {
		entries, err := os.ReadDir(ep.ReplayPath)
		if err != nil {
			if os.IsNotExist(err) {
				return nil, nil
			}
			return nil, err
		}
		type item struct {
			name string
			mt   time.Time
		}
		var items []item
		for _, e := range entries {
			if !e.IsDir() && strings.HasSuffix(e.Name(), ".bin") {
				if st, err := e.Info(); err == nil {
					items = append(items, item{e.Name(), st.ModTime()})
				}
			}
		}
		sort.Slice(items, func(i, j int) bool { return items[i].mt.After(items[j].mt) })
		out := make([]string, len(items))
		for i, v := range items {
			out[i] = v.name
		}
		return out, nil
	}
	script := fmt.Sprintf(`ls -t1 %s/*.bin 2>/dev/null`, shellQuote(ep.ReplayPath))
	_, out, err := runADB(ep.ID, 20*time.Second, "shell", script)
	if err != nil {
		return nil, err
	}
	var result []string
	for _, l := range strings.Split(out, "\n") {
		l = strings.TrimSpace(l)
		if l != "" {
			result = append(result, filepath.Base(l))
		}
	}
	return result, nil
}

func runADB(serial string, timeout time.Duration, args ...string) (int, string, error) {
	aa := append([]string{}, args...)
	if serial != "" {
		aa = append([]string{"-s", serial}, aa...)
	}
	return runCommand(timeout, adbPath, aa...)
}

func (c *RibeiroCLI) fsPull(ep Endpoint, remoteFile, localDst string) error {
	if ep.Kind == "local" {
		return copyFile(filepath.Join(ep.ReplayPath, remoteFile), localDst)
	}
	code, _, err := runADB(ep.ID, 60*time.Second, "pull", filepath.Join(ep.ReplayPath, remoteFile), localDst)
	if err != nil || code != 0 {
		if err != nil {
			return err
		}
		return fmt.Errorf("adb pull fallo: %d", code)
	}
	return nil
}

func (c *RibeiroCLI) fsPushTmp(ep Endpoint, localSrc, tmpRemote string) error {
	if ep.Kind == "local" {
		return copyFile(localSrc, tmpRemote)
	}
	code, _, err := runADB(ep.ID, 60*time.Second, "push", localSrc, tmpRemote)
	if err != nil || code != 0 {
		if err != nil {
			return err
		}
		return fmt.Errorf("adb push fallo: %d", code)
	}
	return nil
}

func (c *RibeiroCLI) fsStatStr(ep Endpoint, path string) (string, error) {
	if ep.Kind == "local" {
		code, out, err := runCommand(15*time.Second, "stat", path)
		if err != nil || code != 0 {
			if err != nil {
				return out, err
			}
			return out, fmt.Errorf("stat fallo: %d", code)
		}
		return out, nil
	}
	_, out, err := runADB(ep.ID, 20*time.Second, "shell", fmt.Sprintf("stat %s", shellQuote(path)))
	return out, err
}

func (c *RibeiroCLI) fsShell(ep Endpoint, script string, check bool, timeout time.Duration) (string, error) {
	var code int
	var out string
	var err error
	if ep.Kind == "local" {
		code, out, err = runCommand(timeout, "sh", "-c", script)
	} else {
		code, out, err = runADB(ep.ID, timeout, "shell", script)
	}
	if err != nil {
		if check {
			return out, err
		}
		return out, nil
	}
	if check && code != 0 {
		return out, fmt.Errorf("shell fallo: %d\n%s", code, out)
	}
	return out, nil
}

func (c *RibeiroCLI) fsFfrtcPath(ep Endpoint) string {
	suffix := "Android/data/com.dts.freefireth/files/ffrtc_log.txt"
	if ep.Version == "max" {
		suffix = "Android/data/com.dts.freefiremax/files/ffrtc_log.txt"
	}
	if ep.Kind == "local" {
		return "/sdcard/" + suffix
	}
	return "/storage/emulated/0/" + suffix
}
func (c *RibeiroCLI) fsReplayDir(ep Endpoint) string { return ep.ReplayPath }

func (c *RibeiroCLI) log(msg, tag string) {
	if msg == "" {
		fmt.Println()
		return
	}
	colors := map[string]string{"info": CYAN, "success": GREEN + BOLD, "error": RED + BOLD, "warning": YELLOW, "secure": BLUE + BOLD, "verdict_original": GREEN + BOLD, "file_path": ""}
	prefixes := map[string]string{"info": "[INFO]", "success": "[OK]", "error": "[ERROR]", "warning": "[AVISO]", "secure": "[SEGURO]", "verdict_original": "[VEREDICTO]", "file_path": ""}
	col := colors[tag]
	pre := prefixes[tag]
	if pre != "" {
		fmt.Printf("%s%s%s %s%s%s\n", col, pre, RESET, col, msg, RESET)
	} else {
		fmt.Printf("%s%s%s\n", col, msg, RESET)
	}
}
func (c *RibeiroCLI) logDetail(msg, tag string) {
	if os.Getenv("RIBEIRO_VERBOSE") == "1" {
		c.log(msg, tag)
	}
}

func (c *RibeiroCLI) buildReplayCommitCommand(replayPath, targetBin, targetJSON, tmpBin, tmpJSON, suffix string) (string, error) {
	if suffix == "" {
		return "", errors.New("Sufijo de replay no válido para la transferencia.")
	}
	for _, r := range suffix {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || strings.ContainsRune("._-", r)) {
			return "", errors.New("Sufijo de replay no válido para la transferencia.")
		}
	}
	assignments := []string{"set -e", fmt.Sprintf("REPLAY_DIR=%s", shellQuote(replayPath)), fmt.Sprintf("TARGET_BIN=%s", shellQuote(targetBin)), fmt.Sprintf("TARGET_JSON=%s", shellQuote(targetJSON)), fmt.Sprintf("TMP_BIN=%s", shellQuote(tmpBin)), fmt.Sprintf("TMP_JSON=%s", shellQuote(tmpJSON)), fmt.Sprintf("SUFFIX=%s", shellQuote(suffix))}
	script := `
TMP_READY="${TMP_JSON}.ready_$$"
PULSE="${REPLAY_DIR}/.sync_pulse_$$"
trap 'rm -f "$TMP_READY" "$TMP_BIN" "$TMP_JSON" "$PULSE"' EXIT
[ -f "$TARGET_BIN" ]
[ -f "$TARGET_JSON" ]
[ -f "$TMP_BIN" ]
[ -f "$TMP_JSON" ]
JSON_TEMPLATE=$(cat "$TMP_JSON")
BIN_OWNER=$(stat -c '%u:%g' "$TARGET_BIN")
JSON_OWNER=$(stat -c '%u:%g' "$TARGET_JSON")
BIN_MODE=$(stat -c '%a' "$TARGET_BIN")
JSON_MODE=$(stat -c '%a' "$TARGET_JSON")
DIR_ACCESS=$(stat -c '%x' "$REPLAY_DIR")
CURRENT_BIN="$TARGET_BIN"
CURRENT_JSON="$TARGET_JSON"
FIRST_ROUND=1
ROUND=0
while [ "$ROUND" -lt 5 ]; do
    TARGET_EPOCH=$(($(date +%s) + 2))
    TS_NAME=$(date -d "@$TARGET_EPOCH" +%Y-%m-%d-%H-%M-%S)
    NEW_NAME="${TS_NAME}_${SUFFIX}"
    MATCH_DT="${NEW_NAME%_rep.bin}"
    NEW_BIN="${REPLAY_DIR}/${NEW_NAME}"
    NEW_JSON="${REPLAY_DIR}/${NEW_NAME%.bin}.json"
    while [ -e "$NEW_BIN" ] || [ -e "$NEW_JSON" ]; do
        TARGET_EPOCH=$(($TARGET_EPOCH + 1))
        TS_NAME=$(date -d "@$TARGET_EPOCH" +%Y-%m-%d-%H-%M-%S)
        NEW_NAME="${TS_NAME}_${SUFFIX}"
        MATCH_DT="${NEW_NAME%_rep.bin}"
        NEW_BIN="${REPLAY_DIR}/${NEW_NAME}"
        NEW_JSON="${REPLAY_DIR}/${NEW_NAME%.bin}.json"
    done
    printf '%s' "$JSON_TEMPLATE" | sed -e "s|MAKAP_MATCH_DT|$MATCH_DT|g" -e "s|MAKAP_NAME_FILE|$NEW_NAME|g" > "$TMP_READY"
    if [ "$FIRST_ROUND" -eq 1 ]; then cat "$TMP_BIN" > "$CURRENT_BIN"; FIRST_ROUND=0; fi
    cat "$TMP_READY" > "$CURRENT_JSON"
    rm -f "$TMP_READY" "$TMP_BIN" "$TMP_JSON"
    mv "$CURRENT_BIN" "$NEW_BIN"
    if ! mv "$CURRENT_JSON" "$NEW_JSON"; then mv "$NEW_BIN" "$CURRENT_BIN"; exit 31; fi
    CURRENT_BIN="$NEW_BIN"; CURRENT_JSON="$NEW_JSON"
    while [ "$(date +%s)" -lt "$TARGET_EPOCH" ]; do sleep 0.005; done
    ATTEMPT=0
    while [ "$ATTEMPT" -lt 32 ] && [ "$(date +%s)" -eq "$TARGET_EPOCH" ]; do
        : > "$PULSE"
        (rm -f "$PULSE" & touch "$CURRENT_BIN" "$CURRENT_JSON" & wait)
        if stat -c '%x|%y|%z' "$CURRENT_BIN" "$CURRENT_JSON" "$REPLAY_DIR" | awk -F'|' 'NR==1 { ref=$1; ok=($1==$2 && $2==$3) } NR==2 { ok=ok && ref==$1 && $1==$2 && $2==$3 } NR==3 { ok=ok && ref==$2 && $2==$3 } END { exit !ok }'; then
            [ "$(stat -c '%u:%g' "$CURRENT_BIN")" = "$BIN_OWNER" ]
            [ "$(stat -c '%u:%g' "$CURRENT_JSON")" = "$JSON_OWNER" ]
            [ "$(stat -c '%a' "$CURRENT_BIN")" = "$BIN_MODE" ]
            [ "$(stat -c '%a' "$CURRENT_JSON")" = "$JSON_MODE" ]
            [ "$(stat -c '%x' "$REPLAY_DIR")" = "$DIR_ACCESS" ]
            trap - EXIT
            printf '%s\n' "$CURRENT_BIN"
            exit 0
        fi
        ATTEMPT=$(($ATTEMPT + 1))
    done
    ROUND=$(($ROUND + 1))
done
echo 'No fue posible converger las marcas de tiempo en el mismo segundo.' >&2
exit 32
`
	return strings.Join(assignments, "\n") + script, nil
}

func copyFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	if err := os.MkdirAll(filepath.Dir(dst), 0755); err != nil && filepath.Dir(dst) != "." {
		return err
	}
	out, err := os.Create(dst)
	if err != nil {
		return err
	}
	defer out.Close()
	_, err = io.Copy(out, in)
	if err != nil {
		return err
	}
	return out.Close()
}

func (c *RibeiroCLI) syncEndpoints(endpoints Endpoints, specificFile string, retryCount int) {
	src, tgt := endpoints.Source, endpoints.Target
	files := []string{}
	if specificFile != "" {
		files = []string{specificFile}
	} else {
		bins, err := c.fsListBins(src)
		if err != nil {
			c.log("[ERROR] "+err.Error(), "error")
			return
		}
		key := src.Kind + "_" + src.ID
		if _, ok := c.lastFilesSource[key]; !ok {
			m := map[string]struct{}{}
			for _, b := range bins {
				m[b] = struct{}{}
			}
			c.lastFilesSource[key] = m
			return
		}
		m := map[string]struct{}{}
		for _, b := range bins {
			m[b] = struct{}{}
		}
		for b := range m {
			if _, old := c.lastFilesSource[key][b]; !old {
				files = append(files, b)
			}
		}
		c.lastFilesSource[key] = m
	}
	if len(files) == 0 {
		return
	}
	c.log("INICIANDO ENVÍO DE REPLAY (CEL -> CEL)...", "info")
	for _, fileName := range files {
		if !strings.HasSuffix(fileName, ".bin") {
			continue
		}
		c.logDetail("[DATO] PROCESANDO PAQUETE: "+truncate(fileName, 10)+"...", "info")
		tmpRoot, err := os.MkdirTemp("", "wall-sync-")
		if err != nil {
			c.log(err.Error(), "error")
			return
		}
		defer os.RemoveAll(tmpRoot)
		localTmpBin := filepath.Join(tmpRoot, "source.bin")
		localTmpJSON := filepath.Join(tmpRoot, "source.json")
		localTargetJSON := filepath.Join(tmpRoot, "target.json")
		targetSuffix := ""
		if i := strings.Index(fileName, "_"); i >= 0 {
			targetSuffix = fileName[i+1:]
		}
		replayPath := tgt.ReplayPath
		var duplicateBin, duplicateJSON string
		if targetSuffix != "" {
			out, _ := c.fsShell(tgt, fmt.Sprintf("find %s -maxdepth 1 -type f -name '*%s' 2>/dev/null", shellQuote(replayPath), shellQuote(targetSuffix)), false, 15*time.Second)
			if lines := nonEmptyLines(out); len(lines) > 0 {
				duplicateBin = lines[0]
				duplicateJSON = strings.Replace(duplicateBin, ".bin", ".json", 1)
			}
		}
		var targetBin, targetJSON string
		listOut, _ := c.fsShell(tgt, fmt.Sprintf("find %s -maxdepth 1 -name '*.json' -printf '%%T@ %%p\\n' 2>/dev/null | sort -rn | awk '{print $2}'", shellQuote(replayPath)), false, 20*time.Second)
		targetFiles := nonEmptyLines(listOut)
		if retryCount > 0 && len(targetFiles) > 0 {
			targetJSON = targetFiles[0]
			targetBin = strings.Replace(targetJSON, ".json", ".bin", 1)
		} else if duplicateJSON != "" {
			targetJSON = duplicateJSON
			targetBin = duplicateBin
		} else if len(targetFiles) > 0 {
			targetJSON = targetFiles[len(targetFiles)-1]
			targetBin = strings.Replace(targetJSON, ".json", ".bin", 1)
		}
		if targetBin == "" || targetJSON == "" {
			c.log("[ALERTA] No se encontró replay base para sobrescribir.", "warning")
			continue
		}
		if err := c.fsPull(src, fileName, localTmpBin); err != nil {
			c.log("[ERROR] "+err.Error(), "error")
			continue
		}
		if err := c.fsPull(src, strings.TrimSuffix(fileName, ".bin")+".json", localTmpJSON); err != nil {
			c.log("[ERROR] "+err.Error(), "error")
			continue
		}
		sourceData, err := loadJSON(localTmpJSON)
		if err != nil {
			c.log("[ALERTA] JSON de source inválido: "+err.Error(), "warning")
			continue
		}
		receiverVersion := ""
		if tgt.Kind == "local" {
			if err := copyFile(targetJSON, localTargetJSON); err != nil {
				c.log("[ALERTA] "+err.Error(), "warning")
				continue
			}
		} else {
			if err := c.fsPull(Endpoint{Kind: "adb", ID: tgt.ID, ReplayPath: filepath.Dir(targetJSON)}, filepath.Base(targetJSON), localTargetJSON); err != nil {
				code, _, e := runADB(tgt.ID, 30*time.Second, "pull", targetJSON, localTargetJSON)
				if e != nil || code != 0 {
					c.log("[ALERTA] no se pudo leer JSON destino", "warning")
					continue
				}
			}
		}
		if td, err := loadJSON(localTargetJSON); err == nil {
			if v, ok := td["Version"]; ok {
				receiverVersion = fmt.Sprint(v)
			}
		}
		if receiverVersion != "" {
			sourceData["Version"] = receiverVersion
		}
		sourceData["MatchDateTime"] = "MAKAP_MATCH_DT"
		sourceData["MatchDateShowTime"] = "MAKAP_MATCH_DT"
		sourceData["IsEmulatorPool"] = false
		sourceData["Is1POptimized"] = true
		sourceData["is_saved"] = false
		sourceData["IsSaved"] = false
		sourceData["FileName"] = "MAKAP_NAME_FILE"
		if err := writeJSON(localTargetJSON, sourceData); err != nil {
			c.log(err.Error(), "error")
			continue
		}
		now := time.Now().UnixMilli()
		tmpBin := fmt.Sprintf("%s/.tmp_google_%d.bin", replayPath, now)
		tmpJSON := strings.TrimSuffix(tmpBin, ".bin") + ".json"
		if err := c.fsPushTmp(tgt, localTmpBin, tmpBin); err != nil {
			c.log("[ALERTA] "+err.Error(), "warning")
			continue
		}
		if err := c.fsPushTmp(tgt, localTargetJSON, tmpJSON); err != nil {
			c.log("[ALERTA] "+err.Error(), "warning")
			continue
		}
		cmd, err := c.buildReplayCommitCommand(replayPath, targetBin, targetJSON, tmpBin, tmpJSON, targetSuffix)
		if err != nil {
			c.log(err.Error(), "error")
			continue
		}
		out, err := c.fsShell(tgt, cmd, true, 60*time.Second)
		if err != nil {
			c.log("[ALERTA] ERROR EN LA TRANSFERENCIA: "+err.Error(), "warning")
			continue
		}
		finalBin := ""
		for i := len(nonEmptyLines(out)) - 1; i >= 0; i-- {
			l := nonEmptyLines(out)[i]
			if strings.HasSuffix(l, ".bin") {
				finalBin = l
				break
			}
		}
		if finalBin == "" {
			c.log("[ALERTA] No se confirmó la ruta final.", "warning")
			continue
		}
		if !c.garantirStatsPerfeitos(tgt, replayPath, finalBin, 10) {
			c.log("[ALERTA] No fue posible confirmar la sincronización exacta de las marcas de tiempo.", "warning")
			continue
		}
		c.log("REPLAY ENVIADO CORRECTAMENTE", "success")
		time.Sleep(150 * time.Millisecond)
		verdict := c.verificarVeredito(tgt, replayPath, finalBin)
		if verdict == 1 {
			c.log("REPLAY 100% SINCRONIZADO", "success")
			c.burlaLogsEndpoints(endpoints)
		} else if verdict == 0 {
			c.log("REPLAY ALTERADO", "error")
			if retryCount < 3 {
				time.Sleep(2 * time.Second)
				c.syncEndpoints(endpoints, fileName, retryCount+1)
			} else {
				c.log("[FALLO] Máximo de reintentos (3) alcanzado.", "error")
			}
		}
	}
}

func (c *RibeiroCLI) garantirStatsPerfeitos(ep Endpoint, replayPath, binPath string, maxTries int) bool {
	jsonPath := strings.Replace(binPath, ".bin", ".json", 1)
	nameTS := strings.Split(filepath.Base(binPath), "_")[0]
	dirStat, err := c.fsStatStr(ep, replayPath)
	if err != nil {
		return false
	}
	dirAccess := extractTimestamps(dirStat)["Access"]
	if dirAccess == "" {
		return false
	}
	check := func() bool {
		b, e1 := c.fsStatStr(ep, binPath)
		j, e2 := c.fsStatStr(ep, jsonPath)
		d, e3 := c.fsStatStr(ep, replayPath)
		if e1 != nil || e2 != nil || e3 != nil {
			return false
		}
		bt, jt, dt := extractTimestamps(b), extractTimestamps(j), extractTimestamps(d)
		ref := bt["Access"]
		if ref == "" {
			return false
		}
		equal := ref == bt["Modify"] && ref == bt["Change"] && ref == jt["Access"] && ref == jt["Modify"] && ref == jt["Change"] && ref == dt["Modify"] && ref == dt["Change"]
		nameOK := false
		if len(nameTS) == 19 {
			needle := nameTS[:10] + " " + nameTS[11:13] + ":" + nameTS[14:16] + ":" + nameTS[17:19]
			nameOK = strings.Contains(ref, needle)
		}
		return equal && dt["Access"] == dirAccess && nameOK
	}
	if check() {
		return true
	}
	for i := 0; i < maxTries; i++ {
		out, err := c.fsShell(ep, "date +%Y-%m-%d-%H-%M-%S", true, 10*time.Second)
		if err != nil {
			return false
		}
		if strings.TrimSpace(out) != nameTS {
			return false
		}
		cmd := fmt.Sprintf(`PULSE="%s/.stats_pulse_$$"; : > "$PULSE" && (rm -f "$PULSE" & touch %s %s & wait)`, replayPath, shellQuote(binPath), shellQuote(jsonPath))
		_, _ = c.fsShell(ep, cmd, false, 15*time.Second)
		if check() {
			return true
		}
	}
	return false
}

// 1=true, 0=false, -1=unknown
func (c *RibeiroCLI) verificarVeredito(ep Endpoint, replayPath, binPath string) int {
	if binPath == "" {
		out, err := c.fsShell(ep, fmt.Sprintf("ls -t %s/*.bin 2>/dev/null | head -1", shellQuote(replayPath)), false, 15*time.Second)
		if err != nil {
			return -1
		}
		binPath = strings.TrimSpace(out)
		if binPath == "" || strings.Contains(binPath, "No such") {
			return -1
		}
	}
	jp := strings.Replace(binPath, ".bin", ".json", 1)
	bs, e1 := c.fsStatStr(ep, binPath)
	js, e2 := c.fsStatStr(ep, jp)
	ds, e3 := c.fsStatStr(ep, replayPath)
	if e1 != nil || e2 != nil || e3 != nil {
		return -1
	}
	b, j, d := extractTimestamps(bs), extractTimestamps(js), extractTimestamps(ds)
	BA, BM, BC := b["Access"], b["Modify"], b["Change"]
	JA, JM, JC := j["Access"], j["Modify"], j["Change"]
	PM, PC := d["Modify"], d["Change"]
	filenameMatch := false
	fn := filepath.Base(binPath)
	parts := strings.SplitN(fn, "_", 2)
	if len(parts) == 2 && len(parts[0]) == 19 {
		p := parts[0]
		needle := p[:10] + " " + p[11:13] + ":" + p[14:16] + ":" + p[17:19]
		filenameMatch = strings.Contains(BA, needle) || strings.Contains(JA, needle)
	}
	if BA != "" && BA == BM && BM == BC && BC == JA && JA == JM && JM == JC && JC == PM && PM == PC && filenameMatch {
		return 1
	}
	return 0
}

func (c *RibeiroCLI) checkTargetEndpoint(endpoints Endpoints) {
	ep := endpoints.Target
	path := ep.ReplayPath
	out, _ := c.fsShell(ep, fmt.Sprintf("find %s -maxdepth 1 -type f -name '*.bin' -printf '%%T@ %%p\\n' 2>/dev/null | sort -rn | awk '{print $2}' | head -1", shellQuote(path)), false, 20*time.Second)
	bin := strings.TrimSpace(out)
	if bin == "" || strings.Contains(bin, "No such") {
		c.log("[ALERTA] NO SE ENCONTRÓ NINGÚN ARCHIVO BIN", "warning")
		return
	}
	jp := strings.Replace(bin, ".bin", ".json", 1)
	for _, p := range []struct{ label, path string }{{"", bin}, {"", jp}, {"Carpeta MReplays", path}} {
		if p.label != "" {
			c.log(p.label, "info")
		}
		raw, err := c.fsStatStr(ep, p.path)
		if err != nil {
			c.log(err.Error(), "error")
			continue
		}
		for _, line := range strings.Split(raw, "\n") {
			line = strings.TrimSpace(line)
			if line != "" {
				tag := "info"
				if strings.HasPrefix(line, "File:") {
					tag = "file_path"
				}
				c.log(line, tag)
			}
		}
		c.log("", "info")
	}
	v := c.verificarVeredito(ep, path, "")
	if v == 1 {
		c.log("VEREDICTO: REPLAY 100% ORIGINAL", "verdict_original")
	} else if v == 0 {
		c.log("VEREDICTO: REPLAY ALTERADO / PASADO", "error")
	} else {
		c.log("[ERROR] No se encontró ningún archivo", "error")
	}
}

func (c *RibeiroCLI) verifyFfrtcEndpoint(endpoints Endpoints, maxOnly bool) {
	ep := endpoints.Target
	c.log("[FFRTC] VERIFICANDO ESTADÍSTICAS DE FFRTC_LOG.TXT...", "info")
	candidates := []struct{ label, path string }{{"MAX", func() string { e := ep; e.Version = "max"; return c.fsFfrtcPath(e) }()}}
	if !maxOnly {
		candidates = append(candidates, struct{ label, path string }{"NORMAL", func() string { e := ep; e.Version = "normal"; return c.fsFfrtcPath(e) }()})
	}
	found := false
	for _, x := range candidates {
		out, _ := c.fsShell(ep, fmt.Sprintf("stat %s 2>&1", shellQuote(x.path)), false, 15*time.Second)
		raw := strings.TrimSpace(out)
		if raw == "" || strings.Contains(raw, "No such file") || strings.Contains(raw, "cannot stat") {
			continue
		}
		found = true
		c.log("", "info")
		c.log("[FFRTC] ["+x.label+"] stat ffrtc_log.txt", "info")
		for _, line := range strings.Split(raw, "\n") {
			line = strings.TrimSpace(line)
			if line == "" {
				continue
			}
			tag := "info"
			if (strings.Contains(line, "Access:") || strings.Contains(line, "Modify:") || strings.Contains(line, "Change:")) && strings.Contains(line, ".000000000") {
				tag = "warning"
			}
			c.log("   "+line, tag)
		}
	}
	if !found {
		scope := "MAX"
		if !maxOnly {
			scope = "MAX y NORMAL"
		}
		c.log("[AVISO] [FFRTC] No se encontró ffrtc_log.txt ("+scope+").", "warning")
		return
	}
	c.log("", "info")
	c.log("[FFRTC] VERIFICACIÓN COMPLETADA.", "success")
}

func (c *RibeiroCLI) manualSendReplay(endpoints Endpoints) {
	src, tgt := endpoints.Source, endpoints.Target
	c.log(fmt.Sprintf("[ENVIO] SOURCE (origen): %s", endpointLabel(src)), "info")
	c.log(fmt.Sprintf("[ENVIO] TARGET (destino): %s", endpointLabel(tgt)), "info")
	files, err := c.fsListBins(src)
	if err != nil {
		c.log(err.Error(), "error")
		return
	}
	if len(files) == 0 {
		c.log("[ERROR] NO SE ENCONTRÓ NINGÚN REPLAY EN SOURCE (origen)", "error")
		return
	}
	latest := files[0]
	if src.Kind == "local" {
		maxTime := time.Time{}
		for _, f := range files {
			p := filepath.Join(src.ReplayPath, f)
			st, err := os.Stat(p)
			if err == nil && strings.HasSuffix(strings.ToLower(f), ".bin") && st.ModTime().After(maxTime) {
				maxTime = st.ModTime()
				latest = f
			}
		}
	}
	if src.Kind == "local" && !fileExists(filepath.Join(src.ReplayPath, strings.TrimSuffix(latest, ".bin")+".json")) {
		c.log("[ERROR] No se encontró el JSON correspondiente: "+strings.TrimSuffix(latest, ".bin")+".json", "error")
		return
	}
	c.log("[ENVIO] Último replay seleccionado: "+latest, "success")
	c.log("[ENVIO] JSON correspondiente: "+strings.TrimSuffix(latest, ".bin")+".json", "info")
	c.syncEndpoints(endpoints, latest, 0)
}

func loadJSON(path string) (map[string]any, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	b = bytes.TrimPrefix(b, []byte{0xEF, 0xBB, 0xBF})
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, err
	}
	return m, nil
}
func writeJSON(path string, m map[string]any) error {
	b, err := json.Marshal(m)
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0644)
}
func fileExists(p string) bool { _, err := os.Stat(p); return err == nil }
func nonEmptyLines(s string) []string {
	var a []string
	for _, l := range strings.Split(s, "\n") {
		l = strings.TrimSpace(l)
		if l != "" {
			a = append(a, l)
		}
	}
	return a
}
func truncate(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}
func endpointLabel(e Endpoint) string {
	if e.ID != "" {
		return e.ID
	}
	return "LOCAL (S9)"
}

// Port of the Python FFRTC block parser / merger used by burla_logs_action.
type LogBlock struct {
	Timestamp, Content string
	Sequence           int
	FromTarget         bool
}

var timestampRE = regexp.MustCompile(`(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<fraction>\d{1,9}))?`)
var commonFields = []string{"Brand", "CPU", "IMEI", "UUID", "sysver", "package", "sdkver", "sdknum", "CPUChip", "commonLibVer", "commonLibHash", "ServerArea", "ffmpeg-support"}

func extractLogTimestamp(line string) string {
	m := timestampRE.FindStringSubmatch(line)
	if len(m) == 0 {
		return ""
	}
	date, timev := m[1], m[2]
	fraction := ""
	if len(m) >= 4 {
		fraction = m[3]
	}
	fraction += strings.Repeat("0", 9-utf8.RuneCountInString(fraction))
	return date + " " + timev + "." + fraction
}
func parseLogBlocks(content string, fromTarget bool) []LogBlock {
	var blocks []LogBlock
	var cur string
	var lines []string
	seq := 0
	for _, line := range strings.SplitAfter(content, "\n") {
		ts := extractLogTimestamp(line)
		if ts != "" {
			if cur != "" {
				blocks = append(blocks, LogBlock{cur, strings.Join(lines, ""), seq, fromTarget})
				seq++
			}
			cur = ts
			lines = []string{line}
		} else if cur != "" {
			lines = append(lines, line)
		}
	}
	if cur != "" {
		blocks = append(blocks, LogBlock{cur, strings.Join(lines, ""), seq, fromTarget})
	}
	return blocks
}
func isDeviceInfoBlock(content string) bool {
	fields := []string{"Brand:", "Model:", "CPU:", "IMEI:", "UUID:", "sysver:", "package:", "sdkver:", "sdknum:", "CPUChip:"}
	count := 0
	for _, f := range fields {
		if strings.Contains(content, f) {
			count++
		}
	}
	return count >= 4 && (strings.Contains(content, "Model:") || strings.Contains(content, "UUID:")) && (strings.Contains(content, "Brand:") || strings.Contains(content, "CPUChip:") || strings.Contains(content, "initialize#NgnEngine.cpp:102"))
}

func extractAllIdentifiers(content string) map[string]string {
	ids := map[string]string{}
	for _, line := range strings.Split(content, "\n") {
		for _, f := range commonFields {
			re := regexp.MustCompile(`\b` + regexp.QuoteMeta(f) + `\s*[:=]\s*([^\s"'|,;]+)`)
			if m := re.FindStringSubmatch(line); len(m) > 1 {
				if ids[f] == "" {
					ids[f] = strings.TrimSpace(m[1])
				}
			}
		}
		re := regexp.MustCompile(`\bModel\s*[:=]\s*(.+?)\s*$`)
		if m := re.FindStringSubmatch(line); len(m) > 1 && ids["Model"] == "" {
			ids["Model"] = strings.TrimSpace(m[1])
		}
		for _, pat := range []string{`output\s+device\s+type\s*:\s*\d+\s+name\s*:\s*(.+?)\s*$`, `isTheWiredHeadsetOn[^\r\n]*?name\s*:\s*(.+?)\s*$`, `isBluetoothA2dpOn[^\r\n]*?name\s*:\s*(.+?)\s*$`, `AudioMgr[^\r\n]*?name\s*:\s*(.+?)\s*$`, `device\s*name\s*[:=]\s*(.+?)\s*$`} {
			rr := regexp.MustCompile(`(?i)` + pat)
			if m := rr.FindStringSubmatch(line); len(m) > 1 && ids["device_name_audio"] == "" {
				ids["device_name_audio"] = strings.TrimSpace(m[1])
			}
		}
	}
	return ids
}
func applyTargetIdentifiers(content string, target, source map[string]string) string {
	r := content
	for k, tv := range target {
		if k == "device_name_audio" {
			continue
		}
		sv := source[k]
		if tv != "" && sv != "" && tv != sv {
			r = strings.ReplaceAll(r, sv, tv)
		}
		re := regexp.MustCompile(`(?m)(\b` + regexp.QuoteMeta(k) + `\s*[:=]\s*)([^\s"'|,;\r\n]+)`)
		r = re.ReplaceAllString(r, `${1}`+escapeReplacement(tv))
	}
	if model := target["Model"]; model != "" {
		if old := source["device_name_audio"]; old != "" && old != model {
			r = strings.ReplaceAll(r, old, model)
		}
		for _, pat := range []string{`(?i)(output\s+device\s+type\s*:\s*\d+\s+name\s*:\s*)([^\r\n]+)`, `(?i)(AudioMgr[^\r\n]*?name\s*:\s*)([^\r\n]+)`} {
			re := regexp.MustCompile(pat)
			r = re.ReplaceAllString(r, `${1}`+escapeReplacement(model))
		}
	}
	return r
}
func escapeReplacement(s string) string {
	return strings.ReplaceAll(strings.ReplaceAll(s, "\\", "\\\\"), "$", "$$")
}
func mergeFfrtc(targetContent, sourceContent string) (string, error) {
	idsT, idsS := extractAllIdentifiers(targetContent), extractAllIdentifiers(sourceContent)
	tb, sb := parseLogBlocks(targetContent, true), parseLogBlocks(sourceContent, false)
	if len(tb) == 0 {
		return "", errors.New("Fecha y hora del primer log del celular TARGET no encontradas.")
	}
	first := tb[0].Timestamp
	var ti, sl []LogBlock
	for _, b := range tb {
		if b.Timestamp >= first && isDeviceInfoBlock(b.Content) {
			ti = append(ti, b)
		}
	}
	for _, b := range sb {
		if b.Timestamp >= first && !isDeviceInfoBlock(b.Content) {
			sl = append(sl, b)
		}
	}
	if len(ti) == 0 {
		return "", errors.New("Bloque de identificación del celular TARGET no encontrado.")
	}
	if len(sl) == 0 {
		return "", fmt.Errorf("No hay logs nuevos en SOURCE después del inicio del FFRTC de TARGET (%s).", first)
	}
	merged := append(sl, ti...)
	sort.SliceStable(merged, func(i, j int) bool {
		if merged[i].Timestamp == merged[j].Timestamp {
			if merged[i].FromTarget != merged[j].FromTarget {
				return merged[i].FromTarget
			}
			return merged[i].Sequence < merged[j].Sequence
		}
		return merged[i].Timestamp < merged[j].Timestamp
	})
	var sbuf strings.Builder
	for _, b := range merged {
		if sbuf.Len() > 0 && !strings.HasSuffix(sbuf.String(), "\n") && b.Content != "" {
			sbuf.WriteByte('\n')
		}
		sbuf.WriteString(b.Content)
	}
	out := sbuf.String()
	if len(idsT) > 0 {
		out = applyTargetIdentifiers(out, idsT, idsS)
	}
	return out, nil
}

func (c *RibeiroCLI) burlaLogsAction(sourceID, targetID string) error {
	s, t, err := c.selectSourceAndTarget(sourceID, targetID)
	if err != nil {
		return err
	}
	eps, err := c.resolveEndpoints(sourceID, targetID, "remote_is_source")
	if err != nil {
		eps = Endpoints{Mode: "adb_adb", Source: Endpoint{Kind: "adb", ID: s, Version: c.sourceVersion, ReplayPath: c.sourceReplayPath}, Target: Endpoint{Kind: "adb", ID: t, Version: c.targetVersion, ReplayPath: c.targetReplayPath}}
	}
	return c.burlaLogsEndpoints(eps)
}

func (c *RibeiroCLI) burlaLogsEndpoints(endpoints Endpoints) error {
	tgt, src := endpoints.Target, endpoints.Source
	packages := []string{"com.dts.freefireth", "com.dts.freefiremax"}
	detect := func(ep Endpoint) (string, string, error) {
		for _, pkg := range packages {
			suffix := fmt.Sprintf("Android/data/%s/files/ffrtc_log.txt", pkg)
			path := suffix
			if ep.Kind == "local" {
				path = "/sdcard/" + suffix
			} else {
				path = "/storage/emulated/0/" + suffix
			}
			out, err := c.fsShell(ep, fmt.Sprintf("[ -f %s ] && printf 1 || printf 0", shellQuote(path)), true, 10*time.Second)
			if err == nil && strings.TrimSpace(out) == "1" {
				return pkg, path, nil
			}
		}
		return "", "", fmt.Errorf("ffrtc_log.txt no encontrado en el endpoint %s", endpointLabel(ep))
	}
	tp, tr, err := detect(tgt)
	if err != nil {
		return err
	}
	sp, sr, err := detect(src)
	if err != nil {
		return err
	}
	c.logDetail(fmt.Sprintf("[BURLA] [LOGS] SOURCE (logs de partidas): %s | TARGET (identidad): %s", filepath.Base(sp), filepath.Base(tp)), "info")
	tmpRoot, err := os.MkdirTemp("", "burla_ffrtc-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tmpRoot)
	targetLocal := filepath.Join(tmpRoot, "target_ffrtc.txt")
	sourceLocal := filepath.Join(tmpRoot, "source_ffrtc.txt")
	modifiedLocal := filepath.Join(tmpRoot, "modified_ffrtc.txt")
	if err := pullArbitrary(c, tgt, tr, targetLocal); err != nil {
		return err
	}
	if err := pullArbitrary(c, src, sr, sourceLocal); err != nil {
		return err
	}
	tc, err := os.ReadFile(targetLocal)
	if err != nil {
		return err
	}
	sc, err := os.ReadFile(sourceLocal)
	if err != nil {
		return err
	}
	if len(tc) == 0 {
		return errors.New("FFRTC de TARGET está vacío.")
	}
	if len(sc) == 0 {
		return errors.New("FFRTC de SOURCE está vacío.")
	}
	merged, err := mergeFfrtc(string(tc), string(sc))
	if err != nil {
		return err
	}
	if err := os.WriteFile(modifiedLocal, []byte(merged), 0644); err != nil {
		return err
	}
	identityBefore, err := remoteIdentity(c, tgt, tr)
	if err != nil {
		return err
	}
	remoteTemp := filepath.Join("/data/local/tmp", ".bootstat_ffrtc_"+randomHex())
	if tgt.Kind == "local" && !fileExists("/data/local/tmp") {
		remoteTemp = filepath.Join(tmpRoot, ".bootstat_ffrtc_"+randomHex())
	}
	if err := pushArbitrary(c, tgt, modifiedLocal, remoteTemp); err != nil {
		return err
	}
	defer func() { _, _ = c.fsShell(tgt, "rm -f "+shellQuote(remoteTemp), false, 15*time.Second) }()
	if _, err := c.fsShell(tgt, fmt.Sprintf("cat %s > %s && rm -f %s", shellQuote(remoteTemp), shellQuote(tr), shellQuote(remoteTemp)), true, 30*time.Second); err != nil {
		return err
	}
	identityAfter, err := remoteIdentity(c, tgt, tr)
	if err != nil {
		return err
	}
	if identityAfter != identityBefore {
		return fmt.Errorf("Inode, UID o GID cambiaron durante la sobrescritura. Antes: %s Después: %s", identityBefore, identityAfter)
	}
	c.log("[BURLA] [OK] Inode:UID:GID preservados: "+identityAfter, "info")
	c.log("FFRTC BURLADA CON ÉXITO", "success")
	return nil
}

func remoteIdentity(c *RibeiroCLI, ep Endpoint, p string) (string, error) {
	out, err := c.fsShell(ep, "stat -c %i:%u:%g "+shellQuote(p), true, 15*time.Second)
	if err != nil {
		return "", err
	}
	out = strings.TrimSpace(out)
	if out == "" {
		return "", errors.New("No fue posible leer inode, UID y GID del FFRTC.")
	}
	return out, nil
}
func pullArbitrary(c *RibeiroCLI, ep Endpoint, remote, dst string) error {
	if ep.Kind == "local" {
		return copyFile(remote, dst)
	}
	code, _, err := runADB(ep.ID, 120*time.Second, "pull", remote, dst)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("adb pull fallo: %d", code)
	}
	return nil
}
func pushArbitrary(c *RibeiroCLI, ep Endpoint, src, dst string) error {
	if ep.Kind == "local" {
		return copyFile(src, dst)
	}
	code, _, err := runADB(ep.ID, 120*time.Second, "push", src, dst)
	if err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("adb push fallo: %d", code)
	}
	return nil
}
func randomHex() string { return fmt.Sprintf("%x", time.Now().UnixNano()) }

func (c *RibeiroCLI) listDevices() {
	devices := c.getADBDevices()
	hasLocal := runtime.GOOS != "windows" && dirExists("/sdcard/Android")
	c.log(BOLD+"Endpoints disponibles:"+RESET, "info")
	if hasLocal {
		c.log("  "+GREEN+"[LOCAL]"+RESET+" S9 (este celular, acceso directo via /sdcard)", "info")
	}
	if len(devices) == 0 {
		c.log("  (ningún dispositivo ADB adicional detectado)", "warning")
	}
	for i, d := range devices {
		c.log(fmt.Sprintf("  %s[ADB #%d]%s %s", BLUE, i, RESET, d), "info")
	}
	if len(devices) == 0 && !hasLocal {
		c.log("Ningún endpoint disponible. Verifica ADB o permisos de /sdcard.", "warning")
		return
	}
	c.log(CYAN+"Usa --source-role=local (S9 como origen) o --source-role=remote (celular ADB como origen)."+RESET, "info")
}

func clear() {
	if runtime.GOOS == "windows" {
		_, _, _ = runCommand(2*time.Second, "cmd", "/c", "cls")
	} else {
		_, _, _ = runCommand(2*time.Second, "clear")
	}
}
func header() {
	fmt.Println()
	fmt.Println(WHITE + BOLD + "WALL ANDROID PRIVATE" + RESET)
	fmt.Println(WHITE + BOLD + "BY UNKNOWN TEAM" + RESET)
	fmt.Println(BLUE + strings.Repeat("-", 48) + RESET)
}
func pause() {
	fmt.Printf("\n%sPresiona Enter para volver al menú...%s", BLUE, RESET)
	_, _ = bufio.NewReader(os.Stdin).ReadString('\n')
}
func askPort(prompt string) string {
	fmt.Print(prompt)
	r := bufio.NewReader(os.Stdin)
	s, _ := r.ReadString('\n')
	return strings.TrimSpace(s)
}
func readInput(prompt string) string {
	fmt.Print(prompt)
	r := bufio.NewReader(os.Stdin)
	s, _ := r.ReadString('\n')
	return strings.TrimSpace(s)
}

func pairDevice() {
	clear()
	header()
	fmt.Println(CYAN + BOLD + "EMPAREJAR DISPOSITIVO" + RESET)
	ip := readInput("Introduce la IP del dispositivo: ")
	port := askPort("Introduce el PUERTO de emparejamiento (ej.: 42787): ")
	code := readInput("Introduce el CÓDIGO de emparejamiento: ")
	if ip == "" || port == "" || code == "" {
		fmt.Println(RED + "Todos los campos son obligatorios." + RESET)
		pause()
		return
	}
	cmd := exec.Command(adbPath, "pair", ip+":"+port)
	cmd.Stdin = strings.NewReader(code + "\n")
	out, _ := cmd.CombinedOutput()
	fmt.Println(strings.TrimSpace(string(out)))
	if cmd.ProcessState.ExitCode() == 0 {
		fmt.Println(CYAN + BOLD + "CONECTAR DESPUÉS DEL EMPAREJAMIENTO" + RESET)
		cp := askPort("Introduce solamente el PUERTO de conexión (ej.: 5555): ")
		if cp != "" {
			_, o, _ := runADB("", 30*time.Second, "connect", ip+":"+cp)
			fmt.Println(o)
		}
	}
	pause()
}
func connectDevice() {
	clear()
	header()
	fmt.Println(CYAN + BOLD + "CONECTAR DISPOSITIVO" + RESET)
	ip := readInput("Introduce la IP del dispositivo: ")
	port := askPort("Introduce el PUERTO de conexión (ej.: 5555): ")
	if ip == "" || port == "" {
		fmt.Println(RED + "La IP y el puerto son obligatorios." + RESET)
		pause()
		return
	}
	_, out, _ := runADB("", 30*time.Second, "connect", ip+":"+port)
	fmt.Println(out)
	pause()
}
func status() {
	clear()
	header()
	fmt.Println(CYAN + BOLD + "ESTADO DEL ADB" + RESET)
	code, out, err := runADB("", 20*time.Second, "devices", "-l")
	if err != nil || code != 0 {
		fmt.Println(RED + "estado: desconectado" + RESET)
		fmt.Println(RED + "modelo: sin dispositivo" + RESET)
		pause()
		return
	}
	d := adbConnectedSerials(out)
	if len(d) == 0 {
		fmt.Println(RED + "estado: desconectado" + RESET)
		fmt.Println(RED + "modelo: sin dispositivo" + RESET)
		pause()
		return
	}
	serial := d[0]
	_, model, _ := runADB(serial, 20*time.Second, "shell", "getprop", "ro.product.model")
	model = strings.TrimSpace(model)
	if model == "" {
		model = "modelo no identificado"
	}
	fmt.Println(GREEN + "estado: conectado" + RESET)
	fmt.Println(GREEN + "modelo: " + model + RESET)
	fmt.Println(BLUE + "serial: " + serial + RESET)
	pause()
}
func listReadOnly() {
	clear()
	header()
	fmt.Println(CYAN + BOLD + "REPLAYS DEL S21 — FREE FIRE MAX" + RESET)
	d := (&RibeiroCLI{}).getADBDevices()
	if len(d) == 0 {
		fmt.Println(YELLOW + "Ningún S21 autorizado por ADB." + RESET)
		pause()
		return
	}
	serial := d[0]
	rp := "/storage/emulated/0/Android/data/com.dts.freefiremax/files/MReplays"
	fmt.Printf("%sDispositivo S21: %s%s\n%sCarpeta: %s%s\n\n", BLUE, serial, RESET, BLUE, rp, RESET)
	_, out, _ := runADB(serial, 30*time.Second, "shell", fmt.Sprintf("find %s -maxdepth 1 -type f -name '*.bin' -o -name '*.json' -printf '%%T@ %%p\\n' 2>/dev/null | sort -rn", shellQuote(rp)))
	for _, l := range nonEmptyLines(out) {
		p := strings.SplitN(l, " ", 2)
		if len(p) == 2 {
			fmt.Println(p[1])
		} else {
			fmt.Println(l)
		}
	}
	pause()
}
func adbTerminal() {
	clear()
	header()
	fmt.Println(CYAN + BOLD + "TERMINAL ADB SHELL" + RESET)
	d := (&RibeiroCLI{}).getADBDevices()
	if len(d) == 0 {
		fmt.Println(RED + "No se encontró ningún dispositivo ADB autorizado." + RESET)
		pause()
		return
	}
	serial := d[0]
	fmt.Println(GREEN + "Conectando al dispositivo: " + serial + RESET)
	cmd := exec.Command(adbPath, "-s", serial, "shell")
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	_ = cmd.Run()
	pause()
}
func selectedADBDevice() (string, error) {
	d := (&RibeiroCLI{}).getADBDevices()
	if len(d) == 0 {
		return "", errors.New("No se encontró ningún dispositivo ADB autorizado.")
	}
	return d[0], nil
}
func runShellSequence(serial string, commands []string) (int, string, error) {
	return runADB(serial, 120*time.Second, "shell", "set -e\n"+strings.Join(commands, "\n"))
}
func showProgress(label string) {
	for p := 0; p <= 100; p += 5 {
		filled := 30 * p / 100
		bar := strings.Repeat("█", filled) + strings.Repeat("░", 30-filled)
		fmt.Printf("\r%s%s: [%s] %3d%%%s", CYAN, label, bar, p, RESET)
		time.Sleep(30 * time.Millisecond)
	}
	fmt.Println()
}

const injectionPackage = "com.dts.freefiremax"
const injectionBase = "/sdcard/Android/data/com.dts.freefiremax"
const injectionFiles = "/sdcard/Android/data/com.dts.freefiremax/files"
const injectionBackup = "/sdcard/Android/data/com.dts.freefiremax/fileslimpa"
const injectionDirtyBackup = "/sdcard/Android/data/com.dts.freefiremax/filessuja"
const injectionArchive = "/sdcard/DCIM/100PINT/Pins/STK-20260527-WA3007.webp"

func injectFiles() {
	clear()
	header()
	fmt.Println(CYAN + BOLD + "INYECTAR" + RESET)
	serial, err := selectedADBDevice()
	if err != nil {
		fmt.Println(RED + err.Error() + RESET)
		pause()
		return
	}
	fmt.Println(BLUE + "Dispositivo seleccionado: " + serial + RESET)
	showProgress("Preparando inyección")
	cmds := []string{"mv " + shellQuote(injectionFiles) + " " + shellQuote(injectionBackup), "unzip -o " + shellQuote(injectionArchive) + " -d " + shellQuote(injectionBase), "cp -r " + shellQuote(injectionBackup) + "/MReplays/. " + shellQuote(injectionFiles) + "/MReplays"}
	code, out, _ := runShellSequence(serial, cmds)
	if strings.TrimSpace(out) != "" {
		fmt.Println(strings.TrimSpace(out))
	}
	if code == 0 {
		fmt.Println(GREEN + "Inyección completada correctamente." + RESET)
	} else {
		fmt.Println(RED + fmt.Sprintf("La inyección falló. El dispositivo devolvió el código %d.", code) + RESET)
	}
	pause()
}
func removeInjectedFiles() {
	clear()
	header()
	fmt.Println(CYAN + BOLD + "RETIRAR" + RESET)
	serial, err := selectedADBDevice()
	if err != nil {
		fmt.Println(RED + err.Error() + RESET)
		pause()
		return
	}
	fmt.Println(BLUE + "Dispositivo seleccionado: " + serial + RESET)
	showProgress("Preparando retirada")
	cmds := []string{"mv " + shellQuote(injectionFiles) + " " + shellQuote(injectionDirtyBackup), "mv " + shellQuote(injectionBackup) + " " + shellQuote(injectionFiles), "rm -rf " + shellQuote(injectionDirtyBackup), "touch " + shellQuote(injectionFiles) + "/temp.txt", "rm -f " + shellQuote(injectionFiles) + "/temp.txt"}
	code, out, _ := runShellSequence(serial, cmds)
	if strings.TrimSpace(out) != "" {
		fmt.Println(strings.TrimSpace(out))
	}
	if code == 0 {
		fmt.Println(GREEN + "Retirada completada correctamente." + RESET)
	} else {
		fmt.Println(RED + fmt.Sprintf("La retirada falló. El dispositivo devolvió el código %d.", code) + RESET)
	}
	pause()
}

func startMenu() {
	for {
		clear()
		header()
		fmt.Println(CYAN + BOLD + "INICIAR" + RESET)
		fmt.Println(BLUE + "[1]" + RESET + " INYECTAR")
		fmt.Println(BLUE + "[2]" + RESET + " RETIRAR")
		fmt.Println(BLUE + "[3]" + RESET + " VOLVER AL MENÚ PRINCIPAL")
		o := readInput("Elige una opción: ")
		switch o {
		case "1":
			injectFiles()
		case "2":
			removeInjectedFiles()
		case "3":
			return
		default:
			fmt.Println(YELLOW + "Opción no válida." + RESET)
			pause()
		}
	}
}
func activateService() {
	clear()
	header()
	fmt.Println(CYAN + BOLD + "ACTIVAR SERVICIOS" + RESET)
	a := strings.ToLower(readInput("¿Quieres activar los servicios? [S/N]: "))
	if a != "s" && a != "sim" {
		fmt.Println(YELLOW + "Servicios no activados." + RESET)
		pause()
		return
	}
	d := (&RibeiroCLI{}).getADBDevices()
	if len(d) == 0 {
		fmt.Println(RED + "No se encontró ningún dispositivo ADB autorizado." + RESET)
		pause()
		return
	}
	serial := d[0]
	props := []string{"AdbDebuggingHandler", "AdbDebuggingManager", "SensorPoseProvider", "UsbDeviceManager", "UsbHostManager", "UsbPortManager", "UsbService", "UsbSettingsManager", "adbd", "stats_log", "usbd", "MtpService", "MtpDatabase", "MtpServer", "UsbMtp", "adb", "AdbService", "AdbDebugging"}
	var failed []string
	for _, p := range props {
		code, out, _ := runADB(serial, 20*time.Second, "shell", "setprop", "log.tag."+p, "S")
		if code != 0 {
			failed = append(failed, p)
			if out != "" {
				fmt.Println(RED + out + RESET)
			}
		}
	}
	code, out, _ := runADB(serial, 20*time.Second, "shell", "logcat", "-c")
	if code != 0 {
		failed = append(failed, "logcat -c")
		if out != "" {
			fmt.Println(RED + out + RESET)
		}
	}
	if len(failed) > 0 {
		fmt.Println(YELLOW + "Servicios activados con fallos en: " + strings.Join(failed, ", ") + RESET)
	} else {
		fmt.Println(GREEN + "Servicios activados correctamente en el dispositivo " + serial + "." + RESET)
	}
	pause()
}
func menu() {
	for {
		clear()
		header()
		fmt.Println(BLUE + "[1]" + RESET + " INICIAR")
		fmt.Println(BLUE + "[2]" + RESET + " EMPAREJAR DISPOSITIVO")
		fmt.Println(BLUE + "[3]" + RESET + " CONECTAR DISPOSITIVO")
		fmt.Println(BLUE + "[4]" + RESET + " ESTADO DEL ADB")
		fmt.Println(BLUE + "[5]" + RESET + " ACTIVAR SERVICIOS")
		fmt.Println(CYAN + "[T]" + RESET + " ABRIR TERMINAL ADB SHELL")
		fmt.Println(RED + "[S]" + RESET + " SALIR")
		o := strings.ToLower(readInput("Elige una opción: "))
		switch o {
		case "1":
			startMenu()
		case "2":
			pairDevice()
		case "3":
			connectDevice()
		case "4":
			status()
		case "5":
			activateService()
		case "t":
			adbTerminal()
		case "s", "0":
			clear()
			fmt.Println(BLUE + "Saliendo..." + RESET)
			return
		default:
			fmt.Println(YELLOW + "Opción no válida." + RESET)
			pause()
		}
	}
}
func updateTermuxEnvironment() bool {
	if p, err := exec.LookPath("pkg"); err == nil {
		fmt.Println(CYAN + BOLD + "PREPARANDO EL ENTORNO" + RESET)
		_, _, _ = runCommand(5*time.Minute, p, "update", "-y")
		_, _, _ = runCommand(5*time.Minute, p, "install", "android-tools", "-y")
		return true
	}
	return true
}
func interactiveMenu() {
	if !isADBAvailable() {
		clear()
		header()
		fmt.Println(RED + BOLD + "[ERROR] ADB no encontrado. Instala android-tools." + RESET)
		pause()
		return
	}
	menu()
}

func printUsage() {
	fmt.Println(`WALL CLI - Transferencia de replay

Uso:
  wall devices
  wall send [opciones]
  wall check-status [opciones]
  wall check-ffrtc [opciones]
  wall burla-ffrtc [opciones]

Opciones:
  --mode auto|mixed|adb_adb
  --local-version normal|max
  --target-version normal|max
  --source-version normal|max
  --source-role local|remote
  --remote-id SERIAL
  --source-id SERIAL
  --target-id SERIAL`)
}

func parseFlags(args []string) (map[string]string, string, error) {
	m := map[string]string{"mode": "auto", "local-version": "normal", "target-version": "normal", "source-version": "normal", "source-role": "remote"}
	cmd := ""
	for i := 0; i < len(args); i++ {
		a := args[i]
		if strings.HasPrefix(a, "--") {
			if i+1 >= len(args) {
				return nil, "", fmt.Errorf("falta valor para %s", a)
			}
			k := strings.TrimPrefix(a, "--")
			m[k] = args[i+1]
			i++
		} else if cmd == "" {
			cmd = a
		} else {
			return nil, "", fmt.Errorf("argumento inesperado: %s", a)
		}
	}
	if cmd == "" {
		return nil, "", errors.New("falta comando")
	}
	return m, cmd, nil
}

func main() {
	if len(os.Args) == 1 {
		updateTermuxEnvironment()
		interactiveMenu()
		return
	}
	m, cmd, err := parseFlags(os.Args[1:])
	if err != nil {
		printUsage()
		return
	}
	if !isADBAvailable() {
		fmt.Println(RED + BOLD + "[ERROR] ADB no encontrado. Instala android-tools." + RESET)
		os.Exit(1)
	}
	cli := NewRibeiroCLI(m["target-version"], m["source-version"], m["mode"], m["local-version"])
	if cmd == "devices" {
		cli.listDevices()
		return
	}
	sourceRole := m["source-role"]
	role := "remote_is_source"
	if sourceRole == "local" {
		role = "local_is_source"
	}
	sourceID, targetID := m["source-id"], m["target-id"]
	if rid := m["remote-id"]; rid != "" {
		if role == "remote_is_source" {
			sourceID = rid
		} else {
			targetID = rid
		}
	}
	eps, err := cli.resolveEndpoints(sourceID, targetID, role)
	if err != nil {
		fmt.Println(RED + BOLD + "[ERROR] " + err.Error() + RESET)
		os.Exit(2)
	}
	switch cmd {
	case "send":
		cli.manualSendReplay(eps)
	case "check-status":
		cli.checkTargetEndpoint(eps)
	case "check-ffrtc":
		cli.verifyFfrtcEndpoint(eps, false)
	case "burla-ffrtc":
		if err := cli.burlaLogsEndpoints(eps); err != nil {
			fmt.Println(RED + "[ERROR] [BURLA] " + err.Error() + RESET)
		}
	default:
		printUsage()
		os.Exit(1)
	}
}

type imageHeaderRenderer struct{}

func renderHeaderImage(path string) bool {
	f, err := os.Open(path)
	if err != nil {
		return false
	}
	defer f.Close()
	img, _, err := image.Decode(f)
	if err != nil {
		return false
	}
	w := 48
	h := img.Bounds().Dy() * w / img.Bounds().Dx() / 2
	if h < 2 {
		h = 2
	}
	for y := 0; y < h*2; y += 2 {
		for x := 0; x < w; x++ {
			sx := img.Bounds().Min.X + x*img.Bounds().Dx()/w
			sy := img.Bounds().Min.Y + y*img.Bounds().Dy()/(h*2)
			c := colorAt(img, sx, sy)
			bc := colorAt(img, sx, img.Bounds().Min.Y+min(y+1, img.Bounds().Dy()-1))
			fmt.Printf("\033[38;2;%d;%d;%dm\033[48;2;%d;%d;%dm▀", c[0], c[1], c[2], bc[0], bc[1], bc[2])
		}
		fmt.Print(RESET + "\n")
	}
	return true
}
func colorAt(img image.Image, x, y int) [3]uint8 {
	r, g, b, _ := img.At(x, y).RGBA()
	return [3]uint8{uint8(r >> 8), uint8(g >> 8), uint8(b >> 8)}
}
func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
func dirExists(p string) bool { st, err := os.Stat(p); return err == nil && st.IsDir() }
