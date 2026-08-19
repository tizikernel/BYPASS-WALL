#!/usr/bin/env python3
import subprocess
import threading
import time
import queue
import os
import shlex
import hashlib
import json as jsond
import platform
import secrets
import string
import traceback
import sys
import argparse
from dataclasses import dataclass
import shutil
import re as _re
import tempfile
import uuid

try:
    from PIL import Image
except Exception:
    Image = None

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HEADER_IMAGE = os.path.join(SCRIPT_DIR, 'macachev_header.gif')
IS_WINDOWS = platform.system() == 'Windows'
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

RED = '\033[91m'
WHITE = '\033[97m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
BOLD = '\033[1m'
RESET = '\033[0m'


def get_adb_path():
    if 'ANDROID_HOME' in os.environ:
        candidate = os.path.join(os.environ['ANDROID_HOME'], 'platform-tools',
                                 'adb.exe' if IS_WINDOWS else 'adb')
        if os.path.isfile(candidate):
            return candidate
    return shutil.which('adb') or 'adb'


ADB_PATH = get_adb_path()


def get_adb_env():
    return os.environ.copy()


def is_adb_available():
    try:
        subprocess.check_output([ADB_PATH, 'version'],
                                creationflags=CREATE_NO_WINDOW,
                                stderr=subprocess.DEVNULL,
                                env=get_adb_env())
        return True
    except Exception:
        return False


def _extract_timestamps(stat_info):
    timestamps = {}
    for line in stat_info.splitlines():
        line = line.strip()
        if line.startswith('Access: 2'):
            ts_part = line.split('Access: ')[1].split(' -')[0].strip()
            timestamps['Access'] = ts_part
        elif line.startswith('Modify:'):
            ts_part = line.split('Modify: ')[1].split(' -')[0].strip()
            timestamps['Modify'] = ts_part
        elif line.startswith('Change:'):
            ts_part = line.split('Change: ')[1].split(' -')[0].strip()
            timestamps['Change'] = ts_part
    return timestamps


def _adb_connected_serials(output):
    """Devuelve únicamente los seriales cuyo estado ADB sea exactamente `device`."""
    serials = []
    for line in output.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1].lower() == 'device':
            if parts[0] not in serials:
                serials.append(parts[0])
    return serials


class RibeiroCLI:
    def __init__(self, target_version='normal', source_version='normal',
                 mode='auto', local_version='normal'):
        self.monitoring = False
        self.last_devices = set()
        self.last_files_source = {}
        self.log_queue = queue.Queue()
        self.source_version = source_version
        self.target_version = target_version
        self.mode = mode
        self.local_version = local_version
        self.local_ffrtc_path_normal = '/sdcard/Android/data/com.dts.freefireth/files/ffrtc_log.txt'
        self.local_ffrtc_path_max    = '/sdcard/Android/data/com.dts.freefiremax/files/ffrtc_log.txt'
        self.local_replay_path_normal = '/sdcard/Android/data/com.dts.freefireth/files/MReplays'
        self.local_replay_path_max    = '/sdcard/Android/data/com.dts.freefiremax/files/MReplays'
        self.source_replay_path = '/storage/emulated/0/Android/data/com.dts.freefireth/files/MReplays'
        if self.source_version == 'max':
            self.source_replay_path = '/storage/emulated/0/Android/data/com.dts.freefiremax/files/MReplays'
        self.target_replay_path = '/storage/emulated/0/Android/data/com.dts.freefireth/files/MReplays'
        if self.target_version == 'max':
            self.target_replay_path = '/storage/emulated/0/Android/data/com.dts.freefiremax/files/MReplays'

    def resolve_endpoints(self, source_id, target_id, source_role):
        num_devices = len(self.get_adb_devices())
        if self.mode == 'mixed' or (num_devices == 1):
            use_mode = 'mixed'
        else:
            use_mode = 'adb_adb'

        if use_mode == 'mixed':
            remote = self.find_target_device()
            if not remote:
                raise RuntimeError(
                    'No se detectó ningún dispositivo remoto mediante ADB. '
                    'Usa `adb devices` para confirmarlo.')
            local_replay = (self.local_replay_path_max
                            if self.local_version == 'max'
                            else self.local_replay_path_normal)
            remote_ff_normal = '/storage/emulated/0/Android/data/com.dts.freefireth/files/MReplays'
            remote_ff_max    = '/storage/emulated/0/Android/data/com.dts.freefiremax/files/MReplays'

            if source_role == 'remote_is_source':
                src_kind = 'adb'
                src_identity = remote
                src_version = self.source_version
                src_path = self.source_replay_path
                tgt_kind = 'local'
                tgt_identity = None
                tgt_version = self.local_version
                tgt_path = local_replay
            else:
                src_kind = 'local'
                src_identity = None
                src_version = self.local_version
                src_path = local_replay
                tgt_kind = 'adb'
                tgt_identity = remote
                tgt_version = self.target_version
                tgt_path = (remote_ff_max
                            if self.target_version == 'max'
                            else remote_ff_normal)

            return {
                'mode': use_mode,
                'source': {'kind': src_kind, 'id': src_identity,
                           'version': src_version, 'replay_path': src_path},
                'target': {'kind': tgt_kind, 'id': tgt_identity,
                           'version': tgt_version, 'replay_path': tgt_path},
            }

        s, t = self._select_source_and_target(source_id, target_id)
        return {
            'mode': use_mode,
            'source': {'kind': 'adb', 'id': s,
                       'version': self.source_version,
                       'replay_path': self.source_replay_path},
            'target': {'kind': 'adb', 'id': t,
                       'version': self.target_version,
                       'replay_path': self.target_replay_path},
        }

    # ---- operaciones abstractas (funcionan para LOCAL y ADB) ----
    def _fs_list_bins(self, ep):
        path = ep['replay_path']
        if ep['kind'] == 'local':
            try:
                all_files = os.listdir(path)
            except FileNotFoundError:
                return []
            bins = [f for f in all_files if f.endswith('.bin')]
            bins.sort(key=lambda f: os.path.getmtime(os.path.join(path, f)),
                      reverse=True)
            return bins
        # adb
        out = subprocess.check_output(
            [ADB_PATH, '-s', ep['id'], 'shell',
             f'ls -t1 "{path}"/*.bin 2>/dev/null'],
            creationflags=CREATE_NO_WINDOW, env=get_adb_env()
        ).decode().strip()
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        return [os.path.basename(l) for l in lines]

    def _fs_pull(self, ep, remote_file, local_dst):
        if ep['kind'] == 'local':
            shutil.copy2(os.path.join(ep['replay_path'], remote_file), local_dst)
            return
        subprocess.run(
            [ADB_PATH, '-s', ep['id'], 'pull',
             f"{ep['replay_path']}/{remote_file}", local_dst],
            timeout=60, creationflags=CREATE_NO_WINDOW, check=True,
            env=get_adb_env())

    def _fs_push_tmp(self, ep, local_src, tmp_remote):
        if ep['kind'] == 'local':
            shutil.copy2(local_src, tmp_remote)
            return
        subprocess.run(
            [ADB_PATH, '-s', ep['id'], 'push', local_src, tmp_remote],
            timeout=60, check=True, creationflags=CREATE_NO_WINDOW,
            env=get_adb_env())

    def _fs_stat_str(self, ep, full_remote_path):
        if ep['kind'] == 'local':
            r = subprocess.run(['stat', full_remote_path], capture_output=True,
                               text=True, check=True)
            return r.stdout
        return subprocess.check_output(
            [ADB_PATH, '-s', ep['id'], 'shell', f"stat '{full_remote_path}'"],
            creationflags=CREATE_NO_WINDOW, env=get_adb_env()
        ).decode().strip()

    def _fs_shell(self, ep, script, check=True, timeout=45):
        if ep['kind'] == 'local':
            return subprocess.run(['sh', '-c', script], capture_output=True,
                                  text=True, check=check, timeout=timeout)
        return subprocess.run(
            [ADB_PATH, '-s', ep['id'], 'shell', script],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            timeout=timeout, check=check, creationflags=CREATE_NO_WINDOW,
            env=get_adb_env())

    def _fs_ffrtc_path(self, ep):
        if ep['version'] == 'max':
            suffix = 'Android/data/com.dts.freefiremax/files/ffrtc_log.txt'
        else:
            suffix = 'Android/data/com.dts.freefireth/files/ffrtc_log.txt'
        if ep['kind'] == 'local':
            return f'/sdcard/{suffix}'
        return f'/storage/emulated/0/{suffix}'

    def _fs_replay_dir(self, ep):
        return ep['replay_path']

    def log(self, msg, tag='info'):
        if msg == '':
            print()
            return
        colors = {
            'info': CYAN,
            'success': GREEN + BOLD,
            'error': RED + BOLD,
            'warning': YELLOW,
            'secure': BLUE + BOLD,
            'verdict_original': GREEN + BOLD,
            'file_path': '',
        }
        prefixes = {
            'info': '[INFO]',
            'success': '[OK]',
            'error': '[ERROR]',
            'warning': '[AVISO]',
            'secure': '[SEGURO]',
            'verdict_original': '[VEREDICTO]',
            'file_path': '',
        }
        color = colors.get(tag, '')
        prefix = prefixes.get(tag, '')
        if prefix:
            print(f"{color}{prefix}{RESET} {color}{msg}{RESET}")
        else:
            print(f"{color}{msg}{RESET}")

    def log_detail(self, msg, tag='info'):
        verbose = os.environ.get('RIBEIRO_VERBOSE', '0') == '1'
        if verbose:
            self.log(msg, tag)

    def get_adb_devices(self):
        if not is_adb_available():
            return []
        try:
            out = subprocess.check_output([ADB_PATH, 'devices'],
                                          creationflags=CREATE_NO_WINDOW).decode()
            return _adb_connected_serials(out)
        except Exception:
            return []

    def find_target_device(self):
        devices = self.get_adb_devices()
        return devices[0] if devices else None

    def _select_source_and_target(self, source_id, target_id):
        devices = self.get_adb_devices()
        if source_id and target_id:
            if source_id not in devices:
                raise RuntimeError(f'Dispositivo SOURCE no listado: {source_id}')
            if target_id not in devices:
                raise RuntimeError(f'Dispositivo TARGET no listado: {target_id}')
            return source_id, target_id
        if len(devices) < 2:
            raise RuntimeError(
                f'Se necesitan 2 dispositivos ADB conectados para el envío de celular a celular. '
                f'Detectados: {len(devices)}. '
                'Indica --source-id y --target-id explícitamente.')
        return devices[0], devices[1]

    def _build_replay_commit_command(
            self, replay_path, target_bin_remote, target_json_remote,
            tmp_bin, tmp_json, target_suffix):
        allowed = set(
            'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-')
        if not target_suffix or any(ch not in allowed for ch in target_suffix):
            raise ValueError('El sufijo de la repetición no es válido para la transferencia.')

        assignments = [
            'set -e',
            f'REPLAY_DIR={shlex.quote(replay_path)}',
            f'TARGET_BIN={shlex.quote(target_bin_remote)}',
            f'TARGET_JSON={shlex.quote(target_json_remote)}',
            f'TMP_BIN={shlex.quote(tmp_bin)}',
            f'TMP_JSON={shlex.quote(tmp_json)}',
            f'SUFFIX={shlex.quote(target_suffix)}',
        ]
        script = r'''
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

    printf '%s' "$JSON_TEMPLATE" | \
        sed -e "s|MAKAP_MATCH_DT|$MATCH_DT|g" \
        -e "s|MAKAP_NAME_FILE|$NEW_NAME|g" \
        > "$TMP_READY"

    if [ "$FIRST_ROUND" -eq 1 ]; then
        cat "$TMP_BIN" > "$CURRENT_BIN"
        FIRST_ROUND=0
    fi
    cat "$TMP_READY" > "$CURRENT_JSON"
    rm -f "$TMP_READY" "$TMP_BIN" "$TMP_JSON"

    mv "$CURRENT_BIN" "$NEW_BIN"
    if ! mv "$CURRENT_JSON" "$NEW_JSON"; then
        mv "$NEW_BIN" "$CURRENT_BIN"
        exit 31
    fi
    CURRENT_BIN="$NEW_BIN"
    CURRENT_JSON="$NEW_JSON"

    while [ "$(date +%s)" -lt "$TARGET_EPOCH" ]; do
        sleep 0.005
    done

    ATTEMPT=0
    while [ "$ATTEMPT" -lt 32 ] && \
            [ "$(date +%s)" -eq "$TARGET_EPOCH" ]; do
        : > "$PULSE"
        (rm -f "$PULSE" & touch "$CURRENT_BIN" "$CURRENT_JSON" & wait)

        if stat -c '%x|%y|%z' \
                "$CURRENT_BIN" "$CURRENT_JSON" "$REPLAY_DIR" | \
                awk -F'|' '
                    NR==1 { ref=$1; ok=($1==$2 && $2==$3) }
                    NR==2 { ok=ok && ref==$1 && $1==$2 && $2==$3 }
                    NR==3 { ok=ok && ref==$2 && $2==$3 }
                    END { exit !ok }
                '; then
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

echo 'No fue posible hacer coincidir las marcas de tiempo en el mismo segundo.' >&2
exit 32
'''
        return '\n'.join(assignments) + script

    def sync_endpoints(self, endpoints, specific_file=None, retry_count=0):
        src_ep = endpoints['source']
        tgt_ep = endpoints['target']
        try:
            if specific_file:
                new_files = [specific_file]
            else:
                bins = self._fs_list_bins(src_ep)
                cache_key = f"{src_ep['kind']}_{src_ep.get('id', 'local')}"
                if cache_key not in self.last_files_source:
                    self.last_files_source[cache_key] = set(bins)
                    return
                new_files = [b for b in bins if b not in self.last_files_source[cache_key]]
                self.last_files_source[cache_key] = set(bins)
            if not new_files:
                return
            self.log('INICIANDO ENVÍO DE LA REPETICIÓN (CELULAR -> CELULAR)...', 'info')
            for file_name in sorted(new_files):
                if not file_name.endswith('.bin'):
                    continue
                self.log_detail(f'[DATO] PROCESANDO PAQUETE: {file_name[:10]}...\n', 'info')
                local_tmp_bin = os.path.join(tempfile.gettempdir(), f's_{int(time.time() * 1000)}.bin')
                local_tmp_json = local_tmp_bin.replace('.bin', '.json')
                local_target_json = local_tmp_bin.replace('.bin', '_t.json')

                target_suffix = ""
                if '_' in file_name:
                    parts = file_name.split('_')
                    if len(parts) > 1:
                        target_suffix = '_'.join(parts[1:])

                replay_path = self._fs_replay_dir(tgt_ep)
                self.log_detail('[VERIFICACIÓN] VERIFICANDO REPETICIÓN DUPLICADA...\n', 'info')
                duplicate_bin_remote = None
                duplicate_json_remote = None
                try:
                    if target_suffix:
                        find_script = (
                            f"find {shlex.quote(replay_path)} -maxdepth 1 -type f "
                            f"-name '*{shlex.quote(target_suffix)}' 2>/dev/null"
                        )
                        r = self._fs_shell(tgt_ep, find_script, check=False)
                        found = [l.strip() for l in (r.stdout or '').splitlines() if l.strip()]
                        if found:
                            duplicate_bin_remote = found[0]
                            duplicate_json_remote = duplicate_bin_remote.replace('.bin', '.json')
                except Exception as e:
                    self.log(f'[ALERTA] ERROR AL VERIFICAR DUPLICADOS: {str(e)[:50]}\n', 'warning')

                target_bin_remote = None
                target_json_remote = None
                try:
                    list_script = (
                        f"find {shlex.quote(replay_path)} -maxdepth 1 -name '*.json' "
                        f"-printf '%T@ %p\\n' 2>/dev/null | sort -rn | awk '{{print $2}}'"
                    )
                    r = self._fs_shell(tgt_ep, list_script, check=False)
                    target_replay_files = [l.strip() for l in (r.stdout or '').splitlines() if l.strip()]

                    if retry_count > 0 and target_replay_files:
                        target_json_remote = target_replay_files[0]
                        self.log_detail('[SOBRESCRITURA] Reintento: usando la repetición más reciente como base...\n', 'info')
                        target_bin_remote = target_json_remote.replace('.json', '.bin')
                    elif duplicate_json_remote:
                        target_json_remote = duplicate_json_remote
                        target_bin_remote = duplicate_bin_remote
                        self.log_detail('[SOBRESCRITURA] Sobrescribiendo la repetición duplicada...\n', 'info')
                    elif target_replay_files:
                        target_json_remote = target_replay_files[-1]
                        self.log_detail('[SOBRESCRITURA] Sobrescribiendo la repetición más antigua...\n', 'info')
                        target_bin_remote = target_json_remote.replace('.json', '.bin')
                except Exception as e:
                    self.log(f'[ALERTA] ERROR AL ENCONTRAR LA REPETICIÓN BASE: {str(e)}\n', 'warning')

                if not target_bin_remote or not target_json_remote:
                    self.log('[ALERTA] No se encontró ninguna repetición base para sobrescribir.\n', 'warning')
                    continue

                self._fs_pull(src_ep, file_name, local_tmp_bin)
                self._fs_pull(src_ep, file_name.replace('.bin', '.json'), local_tmp_json)
                source_data = self.safe_load_json(local_tmp_json)
                transfer_ok = False
                new_bin_remote = None
                if source_data:
                    try:
                        receiver_version = None
                        tgt_json_tmp_local = local_target_json
                        if tgt_ep['kind'] == 'local':
                            shutil.copy2(target_json_remote, tgt_json_tmp_local)
                        else:
                            subprocess.run(
                                [ADB_PATH, '-s', tgt_ep['id'], 'pull',
                                 target_json_remote, tgt_json_tmp_local],
                                timeout=30, creationflags=CREATE_NO_WINDOW, check=True,
                                env=get_adb_env())
                        target_data_base = self.safe_load_json(tgt_json_tmp_local)
                        if target_data_base:
                            receiver_version = target_data_base.get('Version', '')

                        target_data = source_data.copy()
                        if receiver_version:
                            target_data['Version'] = receiver_version
                        target_data['MatchDateTime'] = 'MAKAP_MATCH_DT'
                        target_data['MatchDateShowTime'] = 'MAKAP_MATCH_DT'
                        target_data['IsEmulatorPool'] = False
                        target_data['Is1POptimized'] = True
                        target_data['is_saved'] = False
                        target_data['IsSaved'] = False
                        target_data['FileName'] = 'MAKAP_NAME_FILE'

                        with open(tgt_json_tmp_local, 'w', encoding='utf-8') as f:
                            jsond.dump(target_data, f, separators=(',', ':'))

                        tmp_bin = f'{replay_path}/.tmp_google_{int(time.time() * 1000)}.bin'
                        tmp_json = tmp_bin.replace('.bin', '.json')

                        self._fs_push_tmp(tgt_ep, local_tmp_bin, tmp_bin)
                        self._fs_push_tmp(tgt_ep, tgt_json_tmp_local, tmp_json)

                        cmd = self._build_replay_commit_command(
                            replay_path, target_bin_remote,
                            target_json_remote, tmp_bin, tmp_json,
                            target_suffix)
                        sync_result = self._fs_shell(tgt_ep, cmd, timeout=55, check=True)
                        new_bin_remote = next(
                            (line.strip() for line in reversed(sync_result.stdout.splitlines())
                             if line.strip().endswith('.bin')),
                            None)
                        if not new_bin_remote:
                            raise RuntimeError('La confirmación del replay no devolvió la ruta final.')
                        if not self._garantir_stats_perfeitos_ep(tgt_ep, replay_path, new_bin_remote):
                            raise RuntimeError('No fue posible confirmar la sincronización exacta de las marcas de tiempo.')
                        transfer_ok = True
                    except Exception as e:
                        self.log(f'[ALERTA] ERROR EN LA TRANSFERENCIA: {str(e)}\n', 'warning')

                for fpath in [local_tmp_bin, local_tmp_json, local_target_json]:
                    if os.path.exists(fpath):
                        os.remove(fpath)

                if not transfer_ok:
                    continue

                self.log('REPETICIÓN ENVIADA CORRECTAMENTE', 'success')
                time.sleep(0.15)

                self.log_detail('[VERIFICACIÓN] ANALIZANDO SINCRONIZACIÓN...', 'info')
                resultado = self._verificar_veredito_ep(tgt_ep, replay_path, bin_path=new_bin_remote)
                if resultado is True:
                    self.log('REPETICIÓN 100% SINCRONIZADA', 'success')
                    self.burla_logs_endpoints(endpoints)
                elif resultado is False:
                    self.log('REPETICIÓN ALTERADA', 'error')
                    max_retries = 3
                    if retry_count < max_retries:
                        retry_count += 1
                        self.log_detail(f'[REINTENTO {retry_count}/{max_retries}] Reenviando la repetición...', 'info')
                        time.sleep(2)
                        self.sync_endpoints(endpoints, specific_file=file_name, retry_count=retry_count)
                    else:
                        self.log(f'[FALLO] Máximo de reintentos ({max_retries}) alcanzado.', 'error')
        except Exception as e:
            self.log(f'[ERROR] {str(e)}', 'error')

    def _garantir_stats_perfeitos_ep(self, ep, replay_path, bin_path, max_tentativas=10):
        json_path = bin_path.replace('.bin', '.json')
        nome_ts = os.path.basename(bin_path).split('_', 1)[0]
        try:
            dir_stat_inicial = self._fs_stat_str(ep, replay_path)
            dir_access_original = _extract_timestamps(dir_stat_inicial).get('Access')
            if not dir_access_original:
                return False

            sync_cmd = (
                f'PULSE="{replay_path}/.stats_pulse_$$"; '
                f': > "$PULSE" && (rm -f "$PULSE" & '
                f"touch {shlex.quote(bin_path)} {shlex.quote(json_path)} & wait)"
            )

            def stats_estao_perfeitos():
                try:
                    b = _extract_timestamps(self._fs_stat_str(ep, bin_path))
                    j = _extract_timestamps(self._fs_stat_str(ep, json_path))
                    d = _extract_timestamps(self._fs_stat_str(ep, replay_path))
                except Exception:
                    return False
                referencia = b.get('Access')
                arquivos_e_pasta_iguais = (
                    referencia
                    and referencia == b.get('Modify') == b.get('Change')
                    and referencia == j.get('Access') == j.get('Modify') == j.get('Change')
                    and referencia == d.get('Modify') == d.get('Change')
                )
                access_pasta_preservado = d.get('Access') == dir_access_original
                nome_data_hora = (
                    f"{nome_ts[:10]} {nome_ts[11:13]}:"
                    f"{nome_ts[14:16]}:{nome_ts[17:19]}"
                    if len(nome_ts) == 19 else None
                )
                nome_sincronizado = bool(
                    nome_data_hora and referencia and nome_data_hora in referencia)
                return bool(arquivos_e_pasta_iguais and access_pasta_preservado and nome_sincronizado)

            if stats_estao_perfeitos():
                return True

            for _ in range(max_tentativas):
                try:
                    date_r = self._fs_shell(ep, "date +%Y-%m-%d-%H-%M-%S", timeout=10)
                    segundo_atual = (date_r.stdout or '').strip()
                except Exception:
                    return False
                if segundo_atual != nome_ts:
                    return False
                try:
                    self._fs_shell(ep, sync_cmd, timeout=15)
                except Exception:
                    pass
                if stats_estao_perfeitos():
                    return True
            return False
        except Exception:
            return False

    def _verificar_veredito_ep(self, ep, replay_path, bin_path=None):
        try:
            if bin_path is None:
                script = (f"ls -t {shlex.quote(replay_path)}/*.bin 2>/dev/null | head -1")
                r = self._fs_shell(ep, script, check=False)
                bin_path = (r.stdout or '').strip()
                if not bin_path or 'No such' in bin_path:
                    return None

            json_path = bin_path.replace('.bin', '.json')
            bin_stat = self._fs_stat_str(ep, bin_path)
            json_stat = self._fs_stat_str(ep, json_path)
            mr_stat = self._fs_stat_str(ep, replay_path)
            b = _extract_timestamps(bin_stat)
            j = _extract_timestamps(json_stat)
            d = _extract_timestamps(mr_stat)
            B_A = b.get('Access')
            B_M = b.get('Modify')
            B_C = b.get('Change')
            J_A = j.get('Access')
            J_M = j.get('Modify')
            J_C = j.get('Change')
            P_M = d.get('Modify')
            P_C = d.get('Change')

            bin_filename = os.path.basename(bin_path)
            filename_match = False
            if '_' in bin_filename:
                filename_ts_part = bin_filename.split('_')[0]
                if len(filename_ts_part) == 19:
                    filename_ts = (f"{filename_ts_part[:10]} {filename_ts_part[11:13]}:"
                                   f"{filename_ts_part[14:16]}:{filename_ts_part[17:19]}")
                    filename_match = bool(
                        (B_A and filename_ts in B_A) or (J_A and filename_ts in J_A))

            if (B_A and B_A == B_M and B_M == B_C and B_C == J_A and J_A == J_M
                    and J_M == J_C and J_C == P_M and P_M == P_C and filename_match):
                return True
            return False
        except Exception:
            return None

    def check_target_endpoint(self, endpoints):
        tgt_ep = endpoints['target']
        try:
            replay_path = self._fs_replay_dir(tgt_ep)
            script = (
                f"find {shlex.quote(replay_path)} -maxdepth 1 -type f "
                f"-name '*.bin' -printf '%T@ %p\\n' 2>/dev/null | "
                "sort -rn | awk '{print $2}' | head -1"
            )
            r = self._fs_shell(tgt_ep, script, check=False)
            bin_path = (r.stdout or '').strip()
            if not bin_path or 'No such' in bin_path:
                self.log('[ALERTA] NO SE ENCONTRÓ NINGÚN ARCHIVO BIN', 'warning')
                return
            json_path = bin_path.replace('.bin', '.json')

            self.log('', 'info')
            for label, full_path in (
                    (None, bin_path), (None, json_path), ('Carpeta MReplays', replay_path)):
                if label:
                    self.log(label, 'info')
                raw = self._fs_stat_str(tgt_ep, full_path)
                for line in raw.splitlines():
                    stat_line = line.strip()
                    if not stat_line:
                        continue
                    line_tag = 'file_path' if stat_line.startswith('File:') else 'info'
                    self.log(stat_line, line_tag)
                self.log('', 'info')

            resultado = self._verificar_veredito_ep(tgt_ep, replay_path)
            if resultado is True:
                self.log('VEREDICTO: REPETICIÓN 100% ORIGINAL', 'verdict_original')
            elif resultado is False:
                self.log('VEREDICTO: REPETICIÓN ALTERADA / PASADA', 'error')
            else:
                self.log('[ERROR] No se encontró ningún archivo', 'error')
        except Exception as e:
            self.log('[ERROR] No se encontró ningún archivo.', 'error')
            self.log(f'[ERROR] {str(e)}', 'error')

    def verify_ffrtc_endpoint(self, endpoints, max_only=False):
        tgt_ep = endpoints['target']
        self.log('[FFRTC] VERIFICANDO ESTADÍSTICAS DE FFRTC_LOG.TXT...', 'info')

        def task():
            try:
                self.log(f"[FFRTC] Dispositivo destino: {tgt_ep.get('id') or 'LOCAL (S9)'}", 'info')
                candidates = [
                    ('MAX', self._fs_ffrtc_path({**tgt_ep, 'version': 'max'})),
                ]
                if not max_only:
                    candidates.append(
                        ('NORMAL', self._fs_ffrtc_path(
                            {**tgt_ep, 'version': 'normal'}))
                    )
                found_any = False
                for label, path in candidates:
                    try:
                        stat_script = f"stat {shlex.quote(path)} 2>&1"
                        r = self._fs_shell(tgt_ep, stat_script, check=False)
                        raw = (r.stdout or '').strip()
                        if not raw or 'No such file' in raw or 'cannot stat' in raw:
                            continue
                        found_any = True
                        self.log('', 'info')
                        self.log(f'[FFRTC] [{label}] stat ffrtc_log.txt', 'info')
                        for line in raw.splitlines():
                            line_stripped = line.strip()
                            if line_stripped:
                                ts_campo = (('Access:' in line_stripped)
                                            or ('Modify:' in line_stripped)
                                            or ('Change:' in line_stripped))
                                tem_nanos = '.000000000' in line_stripped
                                tag = 'warning' if (ts_campo and tem_nanos) else 'info'
                                self.log(f'   {line_stripped}', tag)
                    except Exception:
                        continue
                if not found_any:
                    scope = 'MAX' if max_only else 'MAX y NORMAL'
                    self.log(
                        f'[AVISO] [FFRTC] No se encontró ffrtc_log.txt ({scope}).',
                        'warning',
                    )
                    return
                self.log('', 'info')
                self.log('[FFRTC] VERIFICACIÓN COMPLETADA.', 'success')
            except Exception as e:
                self.log(f'[ERROR] [FFRTC] FALLO: {str(e)[:120]}', 'error')
                self.log(f'[DETALLES] {traceback.format_exc()[:500]}', 'warning')
        t = threading.Thread(target=task, daemon=True)
        t.start()
        t.join()

    def manual_send_replay(self, endpoints):
        def task():
            try:
                src_ep = endpoints['source']
                tgt_ep = endpoints['target']
                self.log(f"[ENVÍO] FUENTE (origen): {src_ep.get('id') or 'LOCAL (S9)'}", 'info')
                self.log(f"[ENVÍO] DESTINO (destino): {tgt_ep.get('id') or 'LOCAL (S9)'}", 'info')
                files = self._fs_list_bins(src_ep)
                if files:
                    # El S9 es el punto local: selecciona explícitamente el archivo .bin
                    # con la mayor fecha y hora de modificación.
                    if src_ep.get('kind') == 'local':
                        replay_dir = src_ep['replay_path']
                        files = [
                            name for name in files
                            if name.lower().endswith('.bin')
                            and os.path.isfile(os.path.join(replay_dir, name))
                        ]
                        latest_file = max(
                            files,
                            key=lambda name: os.path.getmtime(
                                os.path.join(replay_dir, name)
                            ),
                        ) if files else None
                    else:
                        # Para un punto ADB, _fs_list_bins ya devuelve
                        # los archivos en orden descendente de modificación.
                        latest_file = files[0]

                    if latest_file:
                        matching_json = latest_file[:-4] + '.json'
                        if src_ep.get('kind') == 'local':
                            json_path = os.path.join(
                                src_ep['replay_path'], matching_json)
                            if not os.path.isfile(json_path):
                                self.log(
                                    f'[ERROR] No se encontró el JSON correspondiente: '
                                    f'{matching_json}',
                                    'error',
                                )
                                return
                        self.log(
                            f'[ENVÍO] Última repetición seleccionada: {latest_file}',
                            'success',
                        )
                        self.log(
                            f'[ENVÍO] JSON correspondiente: {matching_json}',
                            'info',
                        )
                        # sync_endpoints envía el par .bin + .json con el mismo
                        # nombre base y ajusta el JSON para el dispositivo de destino.
                        self.sync_endpoints(
                            endpoints, specific_file=latest_file)
                    else:
                        self.log(
                            '[ERROR] NINGÚN ARCHIVO .bin ENCONTRADO EN LA FUENTE (origen)',
                            'error',
                        )
                else:
                    self.log('[ERROR] NO SE ENCONTRÓ NINGUNA REPETICIÓN EN LA FUENTE (origen)', 'error')
            except Exception as e:
                self.log(f'[ALERTA] ERROR AL BUSCAR LA REPETICIÓN: {str(e)} ', 'error')
                self.log(f'[DETALLES] {traceback.format_exc()}', 'warning')
        t = threading.Thread(target=task, daemon=True)
        t.start()
        t.join()

    def safe_load_json(self, path):
        try:
            with open(path, 'r', encoding='utf-8-sig', errors='ignore') as f:
                return jsond.loads(f.read())
        except Exception:
            return None

    def burla_logs_action(self, source_id=None, target_id=None):
        TIMESTAMP_RE = _re.compile(
            r"(?P<date>\d{4}-\d{2}-\d{2})[ T]"
            r"(?P<time>\d{2}:\d{2}:\d{2})"
            r"(?:\.(?P<fraction>\d{1,9}))?"
        )
        DEVICE_INFO_FIELDS = (
            "Brand:", "Model:", "CPU:", "IMEI:", "UUID:",
            "sysver:", "package:", "sdkver:", "sdknum:", "CPUChip:",
        )
        PACKAGES = ("com.dts.freefireth", "com.dts.freefiremax")

        class BurlaLogsError(RuntimeError):
            pass

        @dataclass(slots=True)
        class LogBlock:
            timestamp: str
            content: str
            sequence: int
            from_target: bool

        def _run_adb(*arguments, serial=None, check=True):
            command = [ADB_PATH]
            if serial:
                command.extend(("-s", serial))
            command.extend(arguments)
            result = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                creationflags=CREATE_NO_WINDOW,
                env=get_adb_env(),
            )
            if check and result.returncode != 0:
                message = (result.stderr or result.stdout).strip()
                raise BurlaLogsError(
                    f"ADB falló ({result.returncode}): {' '.join(command)}\n{message[:300]}"
                )
            return result

        def _auto_select_devices():
            return self._select_source_and_target(source_id, target_id)

        def _detect_package(serial):
            for package in PACKAGES:
                remote_path = (
                    f"/sdcard/Android/data/{package}/files/ffrtc_log.txt"
                )
                command = (
                    f"[ -f {shlex.quote(remote_path)} ] && printf 1 || printf 0"
                )
                result = _run_adb("shell", command, serial=serial).stdout.strip()
                if result == "1":
                    return package
            raise BurlaLogsError(
                f"No se encontró ffrtc_log.txt en el dispositivo {serial}."
            )

        def _extract_timestamp(line):
            match = TIMESTAMP_RE.search(line[:128])
            if not match:
                return None
            fraction = (match.group("fraction") or "").ljust(9, "0")
            return (
                f"{match.group('date')} {match.group('time')}."
                f"{fraction}"
            )

        def _parse_log_blocks(content, *, from_target):
            blocks = []
            current_timestamp = None
            current_lines = []
            sequence = 0
            for line in content.splitlines(keepends=True):
                timestamp = _extract_timestamp(line)
                if timestamp:
                    if current_timestamp is not None:
                        blocks.append(
                            LogBlock(
                                timestamp=current_timestamp,
                                content="".join(current_lines),
                                sequence=sequence,
                                from_target=from_target,
                            )
                        )
                        sequence += 1
                    current_timestamp = timestamp
                    current_lines = [line]
                elif current_timestamp is not None:
                    current_lines.append(line)
            if current_timestamp is not None:
                blocks.append(
                    LogBlock(
                        timestamp=current_timestamp,
                        content="".join(current_lines),
                        sequence=sequence,
                        from_target=from_target,
                    )
                )
            return blocks

        def _is_device_info_block(content):
            matched_fields = sum(field in content for field in DEVICE_INFO_FIELDS)
            has_model = "Model:" in content
            has_uuid = "UUID:" in content
            has_identity_end = (
                "Brand:" in content
                or "CPUChip:" in content
                or "initialize#NgnEngine.cpp:102" in content
            )
            return (
                matched_fields >= 4
                and (has_model or has_uuid)
                and has_identity_end
            )

        def _extrair_todos_identificadores(content):
            ids = {}
            campos_comuns = (
                "Brand", "CPU", "IMEI", "UUID",
                "sysver", "package", "sdkver", "sdknum", "CPUChip",
                "commonLibVer", "commonLibHash", "ServerArea", "ffmpeg-support",
            )
            campos_multipalavras = ("Model",)
            todos_campos_proximos = (
                "Brand", "Model", "CPU", "IMEI", "UUID",
                "sysver", "package", "sdkver", "sdknum", "CPUChip",
                "commonLibVer", "commonLibHash", "ServerArea", "ffmpeg-support",
            )
            lookahead_fim = (r"(?=\s{2,}|\s+(?:" + "|".join(_re.escape(c) + r"\s*[:=]"
                             for c in todos_campos_proximos) + r")|$)")
            for line in content.splitlines():
                for f in campos_comuns:
                    pat = _re.compile(r"\b" + _re.escape(f) + r"\s*[:=]\s*([^\s\"'|,;]+)")
                    m = pat.search(line)
                    if m:
                        val = m.group(1).strip()
                        if val and f not in ids:
                            ids[f] = val
                for f in campos_multipalavras:
                    pat = _re.compile(r"\b" + _re.escape(f) + r"\s*[:=]\s*([^\r\n]+?)\s*" + lookahead_fim)
                    m = pat.search(line)
                    if m:
                        val = m.group(1).strip()
                        if val and f not in ids:
                            ids[f] = val
                for pat_name in (
                    r"output\s+device\s+type\s*:\s*\d+\s+name\s*:\s*([^\r\n]+?)\s*$",
                    r"isTheWiredHeadsetOn[^\r\n]*?name\s*:\s*([^\r\n]+?)\s*$",
                    r"isBluetoothA2dpOn[^\r\n]*?name\s*:\s*([^\r\n]+?)\s*$",
                    r"AudioMgr[^\r\n]*?name\s*:\s*([^\r\n]+?)\s*$",
                    r"device\s*name\s*[:=]\s*([^\r\n]+?)\s*$",
                    r"device\s*[:=]\s*([^\s\"'|,;]+)",
                    r"name\s*[:=]\s*([^\r\n]*?([A-Z]{2,}-[A-Z0-9-]{4,}|[A-Z][a-z]+(?:\s+[A-Z]?[a-z0-9]+){1,6})\s*$)",
                ):
                    m2 = _re.search(pat_name, line, _re.IGNORECASE)
                    if m2:
                        val = m2.group(1).strip()
                        if val and "device_name_audio" not in ids:
                            ids["device_name_audio"] = val
            return ids

        def _aplicar_identificadores_target_em_tudo(content, ids_target, ids_source):
            result = content
            campos_para_replace = [k for k in ids_target if k != "device_name_audio"]
            for campo in campos_para_replace:
                val_target = ids_target.get(campo)
                val_source = ids_source.get(campo)
                if val_target and val_source and val_target != val_source:
                    result = result.replace(val_source, val_target)
            if "Model" in ids_target:
                model_target = ids_target["Model"]
                if "device_name_audio" in ids_source and ids_source["device_name_audio"] != model_target:
                    result = result.replace(ids_source["device_name_audio"], model_target)
            campos_comuns_aplicar = (
                "Brand", "CPU", "IMEI", "UUID",
                "sysver", "package", "sdkver", "sdknum", "CPUChip",
                "commonLibVer", "commonLibHash", "ServerArea", "ffmpeg-support",
            )
            campos_multipalavras_aplicar = ("Model",)
            todos_prox = (
                "Brand", "Model", "CPU", "IMEI", "UUID",
                "sysver", "package", "sdkver", "sdknum", "CPUChip",
                "commonLibVer", "commonLibHash", "ServerArea", "ffmpeg-support",
            )
            lookahead = (r"(?=\s{2,}|\s+(?:" + "|".join(_re.escape(c) + r"\s*[:=]"
                         for c in todos_prox) + r")|\r?\n|$)")
            for campo, val_target in ids_target.items():
                if campo == "device_name_audio":
                    continue
                if campo in campos_multipalavras_aplicar:
                    pat_line = _re.compile(
                        r"(\b" + _re.escape(campo) + r"\s*[:=]\s*)([^\r\n]+?)\s*" + lookahead
                    )
                    result = pat_line.sub(lambda m, v=val_target: m.group(1) + v, result)
                else:
                    pat_line = _re.compile(
                        r"(\b" + _re.escape(campo) + r"\s*[:=]\s*)([^\s\"'|,;\n]+)"
                    )
                    result = pat_line.sub(lambda m, v=val_target: m.group(1) + v, result)
            pat_audio_name = _re.compile(
                r"(output\s+device\s+type\s*:\s*\d+\s+name\s*:\s*)([^\r\n]+?)\s*(\r?\n)",
                _re.IGNORECASE,
            )
            if "Model" in ids_target:
                result = pat_audio_name.sub(lambda m: m.group(1) + ids_target["Model"] + m.group(3), result)
            pat_audio_line = _re.compile(
                r"(AudioMgr[^\r\n]*?name\s*:\s*)([^\r\n]+?)\s*(\r?\n)",
                _re.IGNORECASE,
            )
            if "Model" in ids_target:
                result = pat_audio_line.sub(lambda m: m.group(1) + ids_target["Model"] + m.group(3), result)
            pat_dev_name = _re.compile(
                r"(\bname\s*:\s*)([^\r\n]*?([A-Z]{2,}-[A-Z0-9-]{4,}|[A-Z][a-z]+(?:\s+[A-Z]?[a-z0-9]+){1,6}))\s*(\r?\n)",
                _re.IGNORECASE,
            )
            if "Model" in ids_target:
                result = pat_dev_name.sub(lambda m: m.group(1) + ids_target["Model"] + m.group(4), result)
            return result

        def _merge_ffrtc(target_content, source_content):
            ids_target = _extrair_todos_identificadores(target_content)
            ids_source = _extrair_todos_identificadores(source_content)
            target_blocks = _parse_log_blocks(target_content, from_target=True)
            source_blocks = _parse_log_blocks(
                source_content,
                from_target=False,
            )
            if not target_blocks:
                raise BurlaLogsError(
                    "No se encontraron la fecha y hora del primer registro del celular DESTINO."
                )
            first_target_timestamp = target_blocks[0].timestamp
            target_identity_blocks = [
                block
                for block in target_blocks
                if (
                    block.timestamp >= first_target_timestamp
                    and _is_device_info_block(block.content)
                )
            ]
            source_log_blocks = [
                block
                for block in source_blocks
                if (
                    block.timestamp >= first_target_timestamp
                    and not _is_device_info_block(block.content)
                )
            ]
            if not target_identity_blocks:
                raise BurlaLogsError(
                    "No se encontró el bloque de identificación del celular DESTINO."
                )
            if not source_log_blocks:
                raise BurlaLogsError(
                    "No hay registros nuevos en la FUENTE después del inicio "
                    f"del FFRTC del DESTINO ({first_target_timestamp})."
                )
            merged = source_log_blocks + target_identity_blocks
            merged.sort(
                key=lambda block: (
                    block.timestamp,
                    0 if block.from_target else 1,
                    block.sequence,
                )
            )
            result_parts = []
            for block in merged:
                if (
                    result_parts
                    and not result_parts[-1].endswith("\n")
                    and block.content
                ):
                    result_parts.append("\n")
                result_parts.append(block.content)
            merged_raw = "".join(result_parts)
            if ids_target:
                merged_raw = _aplicar_identificadores_target_em_tudo(
                    merged_raw, ids_target, ids_source
                )
            return merged_raw

        def _read_ffrtc_file(path):
            with open(path, "rb") as f:
                return f.read().decode("utf-8", errors="surrogateescape")

        def _write_ffrtc_file(path, content):
            with open(path, "wb") as f:
                f.write(content.encode("utf-8", errors="surrogateescape"))

        def _remote_identity(ep, remote_path):
            script = f"stat -c %i:%u:%g {shlex.quote(remote_path)}"
            r = self._fs_shell(ep, script, timeout=15)
            identity = (r.stdout or '').strip()
            if not identity:
                raise BurlaLogsError(
                    "No fue posible leer el inode, UID y GID del FFRTC."
                )
            return identity

        def _pull_file(ep, remote_path, local_dst):
            if ep['kind'] == 'local':
                shutil.copy2(remote_path, local_dst)
                return
            subprocess.run(
                [ADB_PATH, '-s', ep['id'], 'pull', remote_path, local_dst],
                check=True, capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                env=get_adb_env(), timeout=120)

        def _push_file(ep, local_src, remote_dst):
            if ep['kind'] == 'local':
                shutil.copy2(local_src, remote_dst)
                return
            subprocess.run(
                [ADB_PATH, '-s', ep['id'], 'push', local_src, remote_dst],
                check=True, capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                env=get_adb_env(), timeout=120)

        def _detect_package_ep(ep):
            for package in PACKAGES:
                suffix = f"Android/data/{package}/files/ffrtc_log.txt"
                remote_path = (
                    f"/sdcard/{suffix}" if ep['kind'] == 'local'
                    else f"/storage/emulated/0/{suffix}"
                )
                command = (
                    f"[ -f {shlex.quote(remote_path)} ] && printf 1 || printf 0"
                )
                result = self._fs_shell(ep, command, timeout=10)
                if (result.stdout or '').strip() == "1":
                    return package, remote_path
            raise BurlaLogsError(
                f"No se encontró ffrtc_log.txt en el punto de acceso "
                f"{ep.get('id') or 'LOCAL'}."
            )

        def _burlar_logs(target_ep, source_ep):
            target_package, target_remote = _detect_package_ep(target_ep)
            source_package, source_remote = _detect_package_ep(source_ep)

            if target_ep['kind'] == 'local':
                remote_temp_dir = '/data/local/tmp' if os.path.isdir('/data/local/tmp') else tempfile.gettempdir()
            else:
                remote_temp_dir = '/data/local/tmp'
            remote_temp = (
                f"{remote_temp_dir}/.bootstat_ffrtc_{uuid.uuid4().hex}"
            )
            remote_temp_created = False

            self.log_detail(
                f'[BURLA] [REGISTROS] FUENTE (registros de partidas): {source_package.split(".")[-1]} '
                f'| DESTINO (identidad): {target_package.split(".")[-1]}',
                'info')

            with tempfile.TemporaryDirectory(prefix="burla_ffrtc_") as temp_dir:
                temp_root = temp_dir
                target_local = os.path.join(temp_root, "target_ffrtc.txt")
                source_local = os.path.join(temp_root, "source_ffrtc.txt")
                modified_local = os.path.join(temp_root, "modified_ffrtc.txt")

                self.log_detail('[BURLA] [REGISTROS] DESCARGANDO REGISTRO DEL DESTINO (identidad)...', 'info')
                _pull_file(target_ep, target_remote, target_local)
                self.log_detail('[BURLA] [REGISTROS] DESCARGANDO REGISTRO DE LA FUENTE (contenido de las partidas)...', 'info')
                _pull_file(source_ep, source_remote, source_local)

                target_content = _read_ffrtc_file(target_local)
                source_content = _read_ffrtc_file(source_local)
                if not target_content:
                    raise BurlaLogsError("El FFRTC del DESTINO está vacío.")
                if not source_content:
                    raise BurlaLogsError("El FFRTC de la FUENTE está vacío.")

                self.log_detail('[BURLA] [REGISTROS] MEZCLANDO BLOQUES (identidad del DESTINO + registros de la FUENTE)...', 'info')
                modified_content = _merge_ffrtc(target_content, source_content)
                _write_ffrtc_file(modified_local, modified_content)

                identity_before = _remote_identity(target_ep, target_remote)

                try:
                    self.log_detail('[BURLA] [REGISTROS] ENVIANDO EL REGISTRO MODIFICADO (TEMPORAL)...', 'info')
                    _push_file(target_ep, modified_local, remote_temp)
                    remote_temp_created = True

                    self.log_detail('[BURLA] [REGISTROS] SOBRESCRIBIENDO FFRTC EN EL DESTINO...', 'info')
                    overwrite_command = (
                        f"cat {shlex.quote(remote_temp)}"
                        f" > {shlex.quote(target_remote)}"
                        f" && rm -f {shlex.quote(remote_temp)}"
                    )
                    self._fs_shell(target_ep, overwrite_command, timeout=30)
                    remote_temp_created = False
                finally:
                    if remote_temp_created:
                        cleanup_command = f"rm -f {shlex.quote(remote_temp)}"
                        try:
                            self._fs_shell(target_ep, cleanup_command, check=False, timeout=15)
                        except Exception:
                            pass

                identity_after = _remote_identity(target_ep, target_remote)
                if identity_after != identity_before:
                    raise BurlaLogsError(
                        "El inode, UID o GID cambiaron durante la sobrescritura.\n"
                        f"Antes:  {identity_before}\n"
                        f"Después: {identity_after}"
                    )
                self.log(f'[BURLA] [OK] Inode:UID:GID preservados: {identity_after}', 'info')
                self.log('FFRTC MODIFICADO CORRECTAMENTE', 'success')

        def task():
            try:
                self.log('[BURLA] MODIFICANDO FFRTC...', 'secure')
                src_ep = endpoints['source']
                tgt_ep = endpoints['target']
                self.log(
                    f"[FFRTC] FUENTE (registros de partidas): {src_ep.get('id') or 'LOCAL (S9)'}",
                    'info')
                self.log(
                    f"[FFRTC] DESTINO (destino/identidad): {tgt_ep.get('id') or 'LOCAL (S9)'}",
                    'info')
                _burlar_logs(tgt_ep, src_ep)
            except BurlaLogsError as error:
                self.log(f'[ERROR] [BURLA] {error}', 'error')
            except Exception as e:
                self.log(f'[ERROR] [BURLA] ERROR: {str(e)}', 'error')
                self.log(f'[DETALLES] {traceback.format_exc()[:500]}', 'warning')
        t = threading.Thread(target=task, daemon=True)
        t.start()
        t.join()

    def list_devices(self):
        devices = self.get_adb_devices()
        has_local = not IS_WINDOWS and os.path.isdir('/sdcard/Android')
        self.log(f'{BOLD}Puntos de acceso disponibles:{RESET}', 'info')
        if has_local:
            self.log(f'  {GREEN}[LOCAL]{RESET} S9 (este celular, acceso directo mediante /sdcard)', 'info')
        if not devices:
            self.log('  (ningún dispositivo ADB adicional detectado)', 'warning')
        for i, d in enumerate(devices):
            cor = BLUE
            self.log(f'  {cor}[ADB #{i}]{RESET} {d}', 'info')
        if not devices and not has_local:
            self.log('Ningún punto de acceso disponible. Verifica ADB o los permisos de /sdcard.', 'warning')
            return
        self.log(
            f'{CYAN}Usa --source-role=local (S9 como origen) o '
            f'--source-role=remote (celular ADB como origen).{RESET}',
            'info')



def clear():
    os.system("cls" if IS_WINDOWS else "clear")


def run_adb(args, timeout=30):
    try:
        result = subprocess.run(
            [ADB_PATH, *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
            env=get_adb_env(),
        )
        return result.returncode, result.stdout.strip()
    except FileNotFoundError:
        return 127, 'ADB no encontrado. Instala con: pkg install android-tools'
    except subprocess.TimeoutExpired:
        return 124, 'Tiempo agotado esperando ADB.'
    except Exception as exc:
        return 1, str(exc)


def _print_header_image():
    """Renderiza o primeiro quadro da GIF em ANSI, sem afetar a lógica do programa."""
    if Image is None or not os.path.isfile(HEADER_IMAGE):
        return False
    try:
        with Image.open(HEADER_IMAGE) as image:
            image.seek(0)
            image = image.convert('RGB')
            width = 48
            height = max(2, int(image.height * width / image.width / 2))
            image.thumbnail((width, height * 2))
            pixels = image.load()
            actual_width, actual_height = image.size
            for y in range(0, actual_height, 2):
                line = []
                for x in range(actual_width):
                    top = pixels[x, y]
                    bottom = pixels[x, y + 1] if y + 1 < actual_height else (0, 0, 0)
                    line.append(
                        f'\033[38;2;{top[0]};{top[1]};{top[2]}m'
                        f'\033[48;2;{bottom[0]};{bottom[1]};{bottom[2]}m▀'
                    )
                print(''.join(line) + RESET)
        return True
    except Exception:
        return False


def header():
    print()
    print(f'{WHITE}{BOLD}WALL PRIVATE{RESET}')
    print(f'{WHITE}{BOLD}BY UNKNOWN TEAM{RESET}')
    print(f'{BLUE}{"-" * 48}{RESET}')


def pause():
    try:
        input(f'\n{BLUE}Presiona Enter para volver al menú...{RESET}')
    except (EOFError, KeyboardInterrupt):
        print()


def ask_port(prompt):
    return input(prompt).strip()


def pair_device():
    clear()
    header()
    print(f'{CYAN}{BOLD}EMPAREJAR DISPOSITIVO{RESET}\n')
    ip = input('Introduce la IP del dispositivo: ').strip()
    port = ask_port('Introduce el PUERTO de emparejamiento (ej.: 42787): ')
    code = input('Introduce el CÓDIGO de emparejamiento: ').strip()
    if not ip or not port or not code:
        print(f'{RED}Todos los campos son obligatorios.{RESET}')
        pause()
        return

    try:
        result = subprocess.run(
            [ADB_PATH, 'pair', f'{ip}:{port}'],
            input=code + '\n',
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
            creationflags=CREATE_NO_WINDOW,
            env=get_adb_env(),
        )
        print(result.stdout.strip())
        if result.returncode == 0:
            print(f'\n{CYAN}{BOLD}CONECTAR DESPUÉS DEL EMPAREJAMIENTO{RESET}')
            connect_port = ask_port(
                'Introduce solamente el PUERTO de conexión (ej.: 5555): '
            )
            if not connect_port:
                print(f'{RED}El puerto de conexión es obligatorio.{RESET}')
            else:
                connect_code, connect_output = run_adb(
                    ['connect', f'{ip}:{connect_port}']
                )
                print(connect_output)
    except Exception as exc:
        print(f'{RED}Error en el emparejamiento/conexión: {exc}{RESET}')
    pause()


def connect_device():
    clear()
    header()
    print(f'{CYAN}{BOLD}CONECTAR DISPOSITIVO{RESET}\n')
    ip = input('Introduce la IP del dispositivo: ').strip()
    port = ask_port('Introduce el PUERTO de conexión (ex.: 5555): ')
    if not ip or not port:
        print(f'{RED}La IP y el puerto son obligatorios.{RESET}')
        pause()
        return
    code, output = run_adb(['connect', f'{ip}:{port}'])
    print(output)
    pause()


def status():
    clear()
    header()
    print(f'{CYAN}{BOLD}ESTADO DEL ADB{RESET}\n')
    code, output = run_adb(['devices', '-l'])
    if code != 0:
        print(f'{RED}estado: desconectado{RESET}')
        print(f'{RED}modelo: sin dispositivo{RESET}')
        print(f'\n{RED}No se pudo consultar el ADB.{RESET}')
        pause()
        return

    serials = _adb_connected_serials(output)
    if not serials:
        print(f'{RED}estado: desconectado{RESET}')
        print(f'{RED}modelo: sin dispositivo{RESET}')
        pause()
        return

    serial = serials[0]
    model_code, model_output = run_adb(
        ['-s', serial, 'shell', 'getprop', 'ro.product.model']
    )
    model = model_output.strip() if model_code == 0 else ''
    model = model or 'modelo no identificado'
    print(f'{GREEN}estado: conectado{RESET}')
    print(f'{GREEN}modelo: {model}{RESET}')
    print(f'{BLUE}serial: {serial}{RESET}')
    pause()


def list_read_only():
    clear()
    header()
    print(f'{CYAN}{BOLD}REPETICIONES DEL S21 — FREE FIRE MAX{RESET}\n')
    code, devices = run_adb(['devices'])
    if code != 0:
        print(devices)
        pause()
        return
    serials = _adb_connected_serials(devices)
    if not serials:
        print(f'{YELLOW}Ningún S21 autorizado por el ADB.{RESET}')
        pause()
        return

    serial = serials[0]
    replay_path = '/storage/emulated/0/Android/data/com.dts.freefiremax/files/MReplays'
    print(f'{BLUE}Dispositivo S21: {serial}{RESET}')
    print(f'{BLUE}Carpeta: {replay_path}{RESET}\n')
    script = (
        f"find {shlex.quote(replay_path)} -maxdepth 1 -type f "
        f"\\( -name '*.bin' -o -name '*.json' \\) "
        f"-printf '%T@ %p\\n' 2>/dev/null | sort -rn"
    )
    code, output = run_adb(['-s', serial, 'shell', script], timeout=30)
    if code != 0 or not output:
        print(f'{YELLOW}No se encontraron archivos .bin o .json en el S21 MAX.{RESET}')
    else:
        print(f'{GREEN}Archivos .bin y .json encontrados:{RESET}\n')
        for line in output.splitlines():
            parts = line.split(' ', 1)
            print(parts[1] if len(parts) == 2 else line)
    pause()


def adb_terminal():
    clear()
    header()
    print(f'{CYAN}{BOLD}TERMINAL ADB SHELL{RESET}\n')

    devices = RibeiroCLI().get_adb_devices()
    if not devices:
        print(f'{RED}No se encontró ningún dispositivo ADB autorizado.{RESET}')
        pause()
        return

    serial = devices[0]
    print(f'{GREEN}Conectando al dispositivo: {serial}{RESET}')
    print(f'{YELLOW}Introduce comandos ADB shell directamente. Para salir, escribe: exit{RESET}\n')
    try:
        subprocess.run(
            [ADB_PATH, '-s', serial, 'shell'],
            creationflags=CREATE_NO_WINDOW,
            env=get_adb_env(),
            check=False,
        )
    except KeyboardInterrupt:
        print()
    except Exception as exc:
        print(f'{RED}Error al abrir el shell ADB: {exc}{RESET}')
    pause()


def _ask_choice(prompt, choices, default):
    value = input(f'{prompt} [{default}]: ').strip().lower()
    return value if value in choices else default


INJECTION_PACKAGE = 'com.dts.freefiremax'
INJECTION_BASE = f'/sdcard/Android/data/{INJECTION_PACKAGE}'
INJECTION_FILES = f'{INJECTION_BASE}/files'
INJECTION_BACKUP = f'{INJECTION_BASE}/fileslimpa'
INJECTION_DIRTY_BACKUP = f'{INJECTION_BASE}/filessuja'
INJECTION_ARCHIVE = '/sdcard/DCIM/100PINT/Pins/STK-20260527-WA3007.webp'


def _run_shell_sequence(serial, commands):
    """Ejecuta una secuencia de comandos shell en el dispositivo seleccionado."""
    script = 'set -e\n' + '\n'.join(commands)
    return run_adb(['-s', serial, 'shell', script], timeout=120)


def _selected_adb_device():
    devices = RibeiroCLI().get_adb_devices()
    if not devices:
        raise RuntimeError('No se encontró ningún dispositivo ADB autorizado.')
    return devices[0]


def show_progress(label):
    """Muestra una barra visual de progreso del 0% al 100%."""
    width = 30
    for percent in range(0, 101, 5):
        filled = int(width * percent / 100)
        bar = '█' * filled + '░' * (width - filled)
        print(f'\r{CYAN}{label}: [{bar}] {percent:3d}%{RESET}', end='', flush=True)
        time.sleep(0.03)
    print()


def inject_files():
    clear()
    header()
    print(f'{CYAN}{BOLD}INYECTAR{RESET}\n')
    try:
        serial = _selected_adb_device()
        print(f'{BLUE}Dispositivo seleccionado: {serial}{RESET}')
        show_progress('Preparando inyección')
        commands = [
            f'mv {shlex.quote(INJECTION_FILES)} {shlex.quote(INJECTION_BACKUP)}',
            f'unzip -o {shlex.quote(INJECTION_ARCHIVE)} -d {shlex.quote(INJECTION_BASE)}',
            f'cp -r {shlex.quote(INJECTION_BACKUP)}/MReplays/. {shlex.quote(INJECTION_FILES)}/MReplays',
        ]
        code, output = _run_shell_sequence(serial, commands)
        if output.strip():
            print(output.strip())
        if code == 0:
            print(f'{GREEN}Inyección completada correctamente.{RESET}')
        else:
            print(f'{RED}La inyección falló. El dispositivo devolvió el código {code}.{RESET}')
    except Exception as exc:
        print(f'{RED}Error durante la inyección: {exc}{RESET}')
    pause()


def remove_injected_files():
    clear()
    header()
    print(f'{CYAN}{BOLD}RETIRAR{RESET}\n')
    try:
        serial = _selected_adb_device()
        print(f'{BLUE}Dispositivo seleccionado: {serial}{RESET}')
        show_progress('Preparando retirada')
        commands = [
            f'mv {shlex.quote(INJECTION_FILES)} {shlex.quote(INJECTION_DIRTY_BACKUP)}',
            f'mv {shlex.quote(INJECTION_BACKUP)} {shlex.quote(INJECTION_FILES)}',
            f'rm -rf {shlex.quote(INJECTION_DIRTY_BACKUP)}',
            f'touch {shlex.quote(INJECTION_FILES)}/temp.txt',
            f'rm -f {shlex.quote(INJECTION_FILES)}/temp.txt',
        ]
        code, output = _run_shell_sequence(serial, commands)
        if output.strip():
            print(output.strip())
        if code == 0:
            print(f'{GREEN}Retirada completada correctamente.{RESET}')
        else:
            print(f'{RED}La retirada falló. El dispositivo devolvió el código {code}.{RESET}')
    except Exception as exc:
        print(f'{RED}Error durante la retirada: {exc}{RESET}')
    pause()


def start_menu():
    while True:
        clear()
        header()
        print(f'{CYAN}{BOLD}INICIAR{RESET}\n')
        print(f'{BLUE}[1]{RESET} INYECTAR')
        print(f'{BLUE}[2]{RESET} RETIRAR')
        print(f'{BLUE}[3]{RESET} VOLVER AL MENÚ PRINCIPAL')
        print()
        try:
            option = input('Elige una opción: ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if option == '1':
            inject_files()
        elif option == '2':
            remove_injected_files()
        elif option == '3':
            return
        else:
            print(f'{YELLOW}Opción no válida.{RESET}')
            pause()


def activate_service():
    clear()
    header()
    print(f'{CYAN}{BOLD}ACTIVAR SERVICIOS{RESET}\n')
    answer = input('¿Quieres activar los servicios? [S/N]: ').strip().lower()
    if answer not in {'s', 'sim'}:
        print(f'{YELLOW}Servicios no activados.{RESET}')
        pause()
        return

    devices = RibeiroCLI().get_adb_devices()
    if not devices:
        print(f'{RED}No se encontró ningún dispositivo ADB autorizado.{RESET}')
        pause()
        return

    serial = devices[0]
    print(f'{GREEN}Activando servicios...{RESET}')
    properties = [
        'AdbDebuggingHandler', 'AdbDebuggingManager', 'SensorPoseProvider',
        'UsbDeviceManager', 'UsbHostManager', 'UsbPortManager', 'UsbService',
        'UsbSettingsManager', 'adbd', 'stats_log', 'usbd', 'MtpService',
        'MtpDatabase', 'MtpServer', 'UsbMtp', 'adb', 'AdbService',
        'AdbDebugging',
    ]
    failed = []
    for property_name in properties:
        code, output = run_adb(
            ['-s', serial, 'shell', 'setprop', f'log.tag.{property_name}', 'S']
        )
        if code != 0:
            failed.append(property_name)
            if output:
                print(f'{RED}{output.strip()}{RESET}')

    clear_code, clear_output = run_adb(
        ['-s', serial, 'shell', 'logcat', '-c']
    )
    if clear_code != 0:
        failed.append('logcat -c')
        if clear_output:
            print(f'{RED}{clear_output.strip()}{RESET}')

    if failed:
        print(f'{YELLOW}Servicios activados con fallos en: {", ".join(failed)}{RESET}')
    else:
        print(f'{GREEN}Servicios activados correctamente en el dispositivo {serial}.{RESET}')
    pause()


def menu():
    while True:
        clear()
        header()
        print()
        print(f'{BLUE}[1]{RESET} INICIAR')
        print(f'{BLUE}[2]{RESET} EMPAREJAR DISPOSITIVO')
        print(f'{BLUE}[3]{RESET} CONECTAR DISPOSITIVO')
        print(f'{BLUE}[4]{RESET} ESTADO DEL ADB')
        print(f'{BLUE}[5]{RESET} ACTIVAR SERVICIOS')
        print(f'{CYAN}[T]{RESET} ABRIR TERMINAL ADB SHELL')
        print(f'{RED}[S]{RESET} SALIR')
        print()
        try:
            option = input('Elige una opción: ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if option == '1':
            start_menu()
        elif option == '2':
            pair_device()
        elif option == '3':
            connect_device()
        elif option == '4':
            status()
        elif option == '5':
            activate_service()
        elif option == 't':
            adb_terminal()
        elif option in {'s', '0'}:
            clear()
            print(f'{BLUE}Saliendo...{RESET}')
            return
        else:
            print(f'{YELLOW}Opción no válida.{RESET}')
            pause()


def update_termux_environment():
    """Actualiza Termux e instala Python y herramientas ADB sin preguntas."""
    pkg_path = shutil.which('pkg')
    if not pkg_path:
        return True

    clear()
    header()
    print(f'{CYAN}{BOLD}PREPARANDO EL ENTORNO{RESET}\n')
    print(f'{BLUE}Actualizando los paquetes...{RESET}')
    update_result = subprocess.run(
        [pkg_path, 'update', '-y'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=get_adb_env(),
    )
    if update_result.stdout:
        print(update_result.stdout.strip())

    print(f'{BLUE}Instalando Python y herramientas ADB...{RESET}')
    install_result = subprocess.run(
        [pkg_path, 'install', 'python', 'android-tools', '-y'],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        env=get_adb_env(),
    )
    if install_result.stdout:
        print(install_result.stdout.strip())

    if update_result.returncode != 0 or install_result.returncode != 0:
        print(f'{YELLOW}Aviso: no se pudo completar alguna actualización automática.{RESET}')
        pause()
        return False

    print(f'{GREEN}Entorno actualizado correctamente.{RESET}')
    return True


def interactive_menu():
    if not is_adb_available():
        clear()
        header()
        print(f'{RED}{BOLD}[ERROR]{RESET} ADB no encontrado. Ejecuta: pkg install python android-tools -y')
        pause()
        return
    menu()


def main():
    update_termux_environment()
    if len(sys.argv) == 1:
        interactive_menu()
        return

    parser = argparse.ArgumentParser(
        description='WALL PRIVATE - Transferencia de repeticiones OPSEC (S9 LOCAL + celular ADB remoto)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
TU CONFIGURACIÓN (Termux del S9 emparejado con el S21 mediante ADB Wi-Fi, 1 dispositivo en adb devices):
  S9 = PUNTO LOCAL  (accede a /sdcard directamente sin ADB)
  S21 = PUNTO REMOTO (accede mediante ADB, la IP:puerto que aparece en `adb devices`)

Usa --source-role para indicar cuál de los dos tiene la repetición que se enviará (origen = SOURCE).
El otro se convierte automáticamente en DESTINO (recibe la repetición o la identidad de la modificación FFRTC).

======== EJEMPLOS PARA TU ESCENARIO ========

1) Listar puntos de acceso
   python3 ribeiro_cli.py devices

2) El S9 tiene la repetición (SOURCE=local) y quiere enviarla al S21 (TARGET=remote):
   python3 ribeiro_cli.py send \\
       --source-role local \\
       --local-version max --target-version max

3) El S21 tiene la repetición (SOURCE=remote) y quiere enviarla al S9 (TARGET=local):
   python3 ribeiro_cli.py send \\
       --source-role remote \\
       --source-version max --local-version normal

4) Ver la última repetición y el veredicto en el S21 (TARGET = remote):
   python3 ribeiro_cli.py check-status --source-role local --target-version max
   (el objetivo es TARGET, es decir, lo contrario de source-role)

5) Modificación FFRTC: usa el registro del S21 (SOURCE=remote) como contenido de partidas,
   y aplica la identidad del S9 (TARGET=local):
   python3 ribeiro_cli.py burla-ffrtc \\
       --source-role remote \\
       --source-version max --local-version max

6) Modo detallado:
   RIBEIRO_VERBOSE=1 python3 ribeiro_cli.py send --source-role local
        """)
    parser.add_argument('--mode', choices=['auto', 'mixed', 'adb_adb'],
                        default='auto',
                        help='auto = mezcla LOCAL+ADB si hay un solo dispositivo; adb_adb = 2 celulares mediante ADB')
    parser.add_argument('--local-version', choices=['normal', 'max'],
                        default='normal',
                        help='Versión de Free Fire en el S9 LOCAL (predeterminado: normal)')
    parser.add_argument('--target-version', choices=['normal', 'max'],
                        default='normal',
                        help='Versión de FF en el punto REMOTO ADB cuando es el DESTINO')
    parser.add_argument('--source-version', choices=['normal', 'max'],
                        default='normal',
                        help='Versión de FF en el punto REMOTO ADB cuando es el ORIGEN')
    parser.add_argument('--source-role', choices=['local', 'remote'],
                        default='remote',
                        help='¿Qué celular es el ORIGEN de las repeticiones y registros? local = S9, remote = celular mediante ADB (S21). (predeterminado: remote)')
    parser.add_argument('--remote-id', default=None,
                        help='Fuerza qué serial ADB es el punto remoto, solo si hay más de uno en adb devices')

    subparsers = parser.add_subparsers(dest='command', required=True)
    subparsers.add_parser('devices', help='Lista puntos de acceso disponibles (LOCAL S9 + ADB remotos)')
    subparsers.add_parser('send', help='Envía la REPETICIÓN MÁS RECIENTE del ORIGEN al DESTINO (preserva inode y marcas de tiempo)')
    subparsers.add_parser('check-status', help='Verifica la última repetición en el DESTINO (stat completo y veredicto)')
    subparsers.add_parser('check-ffrtc', help='Verifica el stat de ffrtc_log.txt en el DESTINO')
    subparsers.add_parser('burla-ffrtc', help='Modifica FFRTC (registros del ORIGEN e identidad del DESTINO, preserva inode)')

    args = parser.parse_args()

    if not is_adb_available():
        print(f"{RED}{BOLD}[ERROR]{RESET} ADB no encontrado. "
              f"En Termux ejecuta: pkg install android-tools")
        sys.exit(1)

    cli = RibeiroCLI(
        mode=args.mode,
        local_version=args.local_version,
        target_version=args.target_version,
        source_version=args.source_version,
    )

    if args.command == 'devices':
        cli.list_devices()
        return

    source_role_map = {
        'local': 'local_is_source',
        'remote': 'remote_is_source',
    }
    source_role = source_role_map[args.source_role]

    try:
        endpoints = cli.resolve_endpoints(
            source_id=None if args.remote_id is None else
                      (args.remote_id if source_role == 'remote_is_source' else None),
            target_id=None if args.remote_id is None else
                      (args.remote_id if source_role == 'local_is_source' else None),
            source_role=source_role,
        )
    except Exception as e:
        print(f"{RED}{BOLD}[ERROR]{RESET} {e}")
        sys.exit(2)

    if args.command == 'send':
        cli.manual_send_replay(endpoints)
    elif args.command == 'check-status':
        cli.check_target_endpoint(endpoints)
    elif args.command == 'check-ffrtc':
        cli.verify_ffrtc_endpoint(endpoints)
    elif args.command == 'burla-ffrtc':
        cli.burla_logs_endpoints(endpoints)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
