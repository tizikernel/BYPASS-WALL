# WALL PRIVATE BY UNKNOWN TEAM

Este proyecto contiene la conversión completa a Go de `Ribeiro00.py`. El programa elimina los emojis y presenta sus mensajes visibles en español. El archivo publicado en el repositorio es el binario `wall`, compilado para Android ARM64 y preparado para ejecutarse en Termux.

## Instalación y ejecución en Termux

```bash
pkg install -y git android-tools
cd ~
git clone https://github.com/tizikernel/BYPASS-WALL.git
cd BYPASS-WALL
chmod +x wall
./wall
```

Si el repositorio ya existe, actualízalo con:

```bash
cd ~/BYPASS-WALL
git pull origin main
chmod +x wall
./wall
```

La preparación automática de paquetes no se ejecuta al iniciar, para evitar esperas largas. Si se necesita realizarla manualmente, puede activarse con:

```bash
WALL_AUTO_UPDATE=1 ./wall
```

## Comandos principales

```bash
./wall devices
./wall --source-role local --local-version max --target-version max send
./wall --source-role remote --source-version max --local-version normal send
./wall --source-role local --target-version max check-status
./wall --source-role remote --source-version max --local-version max check-ffrtc
./wall --source-role remote --source-version max --local-version max burla-ffrtc
```

Las opciones disponibles son `--mode` (`auto`, `mixed` o `adb_adb`), `--local-version`, `--target-version`, `--source-version`, `--source-role` y `--remote-id`. Los nombres de estas opciones y los subcomandos se mantienen sin traducir porque forman parte de la interfaz de comandos.

## Funcionalidad convertida

La conversión incluye la interfaz interactiva, el emparejamiento y la conexión ADB, el estado del dispositivo, la terminal ADB, el listado de repeticiones, la inyección y retirada de archivos, la activación de servicios, la transferencia de archivos `.bin` y `.json`, la preservación de metadatos, la verificación de marcas de tiempo, el análisis de `ffrtc_log.txt` y la combinación de bloques de registros.

También está implementado el flujo `burla-ffrtc`, incluido el enlace entre los puntos de origen y destino que en la fuente Python se invocaba mediante `burla_logs_endpoints` sin una definición explícita.

## Verificación

La implementación Go fue formateada con `gofmt`, validada con `go vet`, comprobada con `go test ./...` y compilada para Linux y Android ARM64. La prueba de ayuda terminó correctamente en menos de cinco segundos y no se encontraron emojis en el código Go. Las operaciones que requieren un dispositivo ADB real deben probarse en Termux con los celulares conectados y autorizados.
