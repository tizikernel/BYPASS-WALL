# WALL Android Private — conversión a Go

Este proyecto contiene la conversión completa de `wall.py` a Go. La implementación está concentrada en `main.go` y utiliza únicamente la biblioteca estándar de Go; no requiere paquetes Go externos ni Python en tiempo de ejecución.

## Compilación

Desde este directorio:

```bash
go build -o wall .
```

El binario generado para Linux x86-64 se entrega junto con el código fuente. Para compilar en otra plataforma puede utilizarse el toolchain de Go correspondiente, por ejemplo:

```bash
GOOS=android GOARCH=arm64 go build -o wall .
```

La ejecución sobre Android/Termux requiere que `adb` esté instalado y disponible en `PATH`, o que `ANDROID_HOME` apunte a un SDK que contenga `platform-tools/adb`. El programa conserva la detección automática de `adb`, la selección de endpoints local/ADB y la variable `RIBEIRO_VERBOSE=1` para el modo detallado. Para ejecutar opcionalmente la preparación automática de paquetes de Termux, usa `WALL_AUTO_UPDATE=1 ./wall`; por defecto no se ejecuta `pkg update` al iniciar, para evitar esperas largas.

## Comandos

```text
./wall devices
./wall --source-role local --local-version max --target-version max send
./wall --source-role remote --source-version max --local-version normal send
./wall --source-role local --target-version max check-status
./wall --source-role remote --source-version max --local-version max check-ffrtc
./wall --source-role remote --source-version max --local-version max burla-ffrtc
```

Las opciones globales disponibles son `--mode` (`auto`, `mixed` o `adb_adb`), `--local-version`, `--target-version`, `--source-version`, `--source-role` y `--remote-id`.

## Funcionalidad convertida

La versión Go incluye la interfaz interactiva, emparejamiento y conexión ADB, estado del dispositivo, terminal ADB, listado de replays, inyección y retirada de archivos, activación de servicios, transferencia de replays, lectura/escritura JSON, preservación de metadatos de archivos, verificación de marcas de tiempo, análisis de `ffrtc_log.txt` y combinación de bloques de logs.

También se incorporó la operación que el archivo Python invocaba como `burla_logs_endpoints` pero no definía explícitamente; en Go está implementada como `burlaLogsEndpoints`, junto con el flujo de `burlaLogsAction`, para que el comando `burla-ffrtc` sea ejecutable y no falle por una referencia inexistente.

El archivo `macachev_header.gif` es opcional. Si se coloca junto al ejecutable, la rutina de renderizado de cabecera puede leerlo; el archivo adjunto original no incluía ese recurso, por lo que la cabecera textual siempre funciona sin él.

## Verificación realizada

La conversión fue formateada con `gofmt`, compilada correctamente, revisada con `go vet` y comprobada con `go test ./...`. La prueba de ayuda y el comando `devices` terminaron en menos de cinco segundos en el entorno de compilación; `devices` informó correctamente que ADB no estaba disponible. La variante publicada se compila para Android ARM64, por lo que debe ejecutarse en Termux sobre un dispositivo Android ARM64.
