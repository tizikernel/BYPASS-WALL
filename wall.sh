#!/data/data/com.termux/files/usr/bin/bash

# ======================================================
#  WALL ANDROID PRIVATE - INYECTOR REMOTO v5
#  BY UNKNOWN TEAM
# ======================================================

RED='\033[1;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;36m'
NC='\033[0m'

REPO_PATH="$HOME/BYPASS-WALL/com.dts.freefireth"
PACKAGE="com.dts.freefireth"
DATA_EXTERNAL="/storage/emulated/0/Android/data/$PACKAGE"
DATA_INTERNAL="/data/data/$PACKAGE"
DEVICE_IP=""
PAIR_PORT=""
CONNECT_PORT="5555"
PAIR_CODE=""
CONNECTED=0

error() { echo -e "${RED}[ERROR] $1${NC}"; exit 1; }
info() { echo -e "${BLUE}[*] $1${NC}"; }
success() { echo -e "${GREEN}[✔] $1${NC}"; }
warning() { echo -e "${YELLOW}[!] $1${NC}"; }

check_deps() {
    command -v adb >/dev/null 2>&1 || error "ADB no instalado. Ejecuta: pkg install android-tools"
    [ -d "$REPO_PATH" ] || error "No se encuentra el repositorio en $REPO_PATH"
}

conectar() {
    clear
    echo "=========== CONEXION WIFI ADB ==========="
    echo -e "${YELLOW}IMPORTANTE: El dispositivo DESTINO debe tener:${NC}"
    echo "  - Depuración inalámbrica ACTIVADA"
    echo "  - Emparejamiento con código ACTIVO (generá uno nuevo si expiró)"
    echo "  - Estar en la MISMA red WiFi que este teléfono"
    echo "==========================================="
    read -p "IP del dispositivo destino (ej. 192.168.1.10): " DEVICE_IP
    read -p "Puerto de emparejamiento (ej. 37000): " PAIR_PORT
    read -p "Codigo de emparejamiento (6 digitos): " PAIR_CODE
    read -p "Puerto de conexion [por defecto 5555]: " CONNECT_PORT
    CONNECT_PORT=${CONNECT_PORT:-5555}

    info "Emparejando con $DEVICE_IP:$PAIR_PORT ..."
    adb pair $DEVICE_IP:$PAIR_PORT $PAIR_CODE 2>/dev/null
    if [ $? -ne 0 ]; then
        warning "Fallo el emparejamiento. Verifica codigo y puerto."
        read -p "Presiona Enter para volver..."
        return 1
    fi
    success "Emparejado correctamente"

    info "Conectando a $DEVICE_IP:$CONNECT_PORT ..."
    adb connect $DEVICE_IP:$CONNECT_PORT 2>/dev/null
    sleep 1
    if adb devices | grep -w "$DEVICE_IP:$CONNECT_PORT" >/dev/null; then
        CONNECTED=1
        success "Conectado a $DEVICE_IP"
    else
        CONNECTED=0
        warning "Fallo la conexion"
    fi
    read -p "Presiona Enter para volver..."
}

desconectar() {
    if [ $CONNECTED -eq 1 ] && [ ! -z "$DEVICE_IP" ]; then
        adb disconnect $DEVICE_IP:$CONNECT_PORT 2>/dev/null
        CONNECTED=0
        success "Desconectado"
    else
        warning "No hay conexion activa"
    fi
    read -p "Presiona Enter para volver..."
}

inyectar() {
    clear
    echo "=========== INYECTANDO ==========="
    if [ $CONNECTED -eq 0 ]; then
        warning "No estas conectado. Ve a CONECTAR primero."
        read -p "Presiona Enter para volver..."
        return 1
    fi

    info "Deteniendo $PACKAGE ..."
    adb shell am force-stop $PACKAGE

    # === COPIA A CARPETA EXTERNA (sin permisos especiales) ===
    info "Copiando carpeta $REPO_PATH a $DATA_EXTERNAL ..."
    adb shell "rm -rf $DATA_EXTERNAL" 2>/dev/null
    adb shell "mkdir -p $DATA_EXTERNAL"
    adb push "$REPO_PATH/"* "$DATA_EXTERNAL/" 2>/dev/null
    if [ $? -eq 0 ]; then
        success "Archivos copiados a $DATA_EXTERNAL"
    else
        warning "Error al copiar a $DATA_EXTERNAL"
    fi

    # === INTENTO DE COPIA A CARPETA INTERNA (con run-as) ===
    info "Intentando copiar a $DATA_INTERNAL con run-as (puede fallar sin root)..."
    adb shell "run-as $PACKAGE rm -rf $DATA_INTERNAL" 2>/dev/null
    adb shell "run-as $PACKAGE mkdir -p $DATA_INTERNAL" 2>/dev/null
    # Copiar archivos temporalmente a /data/local/tmp y luego mover con run-as
    adb push "$REPO_PATH/"* /data/local/tmp/com.dts.freefireth/ 2>/dev/null
    if [ $? -eq 0 ]; then
        adb shell "run-as $PACKAGE cp -r /data/local/tmp/com.dts.freefireth/* $DATA_INTERNAL/" 2>/dev/null
        adb shell "rm -rf /data/local/tmp/com.dts.freefireth" 2>/dev/null
        success "Archivos copiados a $DATA_INTERNAL (con run-as)"
    else
        warning "No se pudo copiar a $DATA_INTERNAL. Solo se copió a $DATA_EXTERNAL."
    fi

    # === PERMISOS (si se copió a externa, ya tiene permisos) ===
    adb shell chmod -R 755 "$DATA_EXTERNAL" 2>/dev/null

    info "Abriendo Free Fire ..."
    adb shell am start -n $PACKAGE/.SplashActivity 2>/dev/null
    if [ $? -eq 0 ]; then
        success "Free Fire iniciado"
    else
        warning "No se pudo abrir, abrelo manual"
    fi
    read -p "Presiona Enter para volver..."
}

bypass() {
    clear
    echo "=========== BYPASS ==========="
    if [ $CONNECTED -eq 0 ]; then
        warning "Conectate primero"
        read -p "Presiona Enter para volver..."
        return 1
    fi
    if [ -f "$REPO_PATH/bypass.sh" ]; then
        adb push "$REPO_PATH/bypass.sh" /data/local/tmp/
        adb shell chmod +x /data/local/tmp/bypass.sh
        adb shell sh /data/local/tmp/bypass.sh
        success "Bypass ejecutado"
    else
        warning "No hay bypass.sh, no hago nada"
    fi
    read -p "Presiona Enter para volver..."
}

reiniciar() {
    clear
    echo "=========== REINICIAR ==========="
    if [ $CONNECTED -eq 0 ]; then
        warning "Conectate primero"
        read -p "Presiona Enter para volver..."
        return 1
    fi
    info "Reiniciando dispositivo..."
    adb reboot
    success "Reinicio enviado"
    CONNECTED=0
    DEVICE_IP=""
    read -p "Presiona Enter para volver..."
}

menu() {
    clear
    printf '\033[1;36m'
    echo "========================================"
    echo "      WALL ANDROID PRIVATE"
    echo "          BY UNKNOWN TEAM"
    echo "========================================"
    printf '\033[0m'
    echo
    if [ $CONNECTED -eq 1 ]; then
        echo -e "[*] Estado: ${GREEN}CONECTADO${NC} a $DEVICE_IP"
    else
        echo -e "[*] Estado: ${RED}DESCONECTADO${NC}"
    fi
    echo
    echo "=========== MENU ==========="
    echo "1) CONECTAR"
    echo "2) INYECTAR"
    echo "3) BYPASS"
    echo "4) REINICIAR"
    echo "5) DESCONECTAR"
    echo "6) SALIR"
    echo "============================="
    read -p "Elegi una opcion: " op
    case $op in
        1) conectar ;;
        2) inyectar ;;
        3) bypass ;;
        4) reiniciar ;;
        5) desconectar ;;
        6) echo "Saliendo..."; exit 0 ;;
        *) warning "Opcion invalida"; sleep 1 ;;
    esac
    menu
}

check_deps
menu
