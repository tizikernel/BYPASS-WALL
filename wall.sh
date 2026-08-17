#!/data/data/com.termux/files/usr/bin/bash

RED='\033[1;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;36m'
NC='\033[0m'

REPO_PATH="$HOME/BYPASS-WALL/com.dts.freefireth"
PACKAGE="com.dts.freefireth"
DATA_PATH="/storage/emulated/0/Android/data/$PACKAGE"
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
    command -v adb >/dev/null 2>&1 || error "ADB no instalado. pkg install android-tools"
    [ -d "$REPO_PATH" ] || error "Repo no encontrado en $REPO_PATH"
}

conectar() {
    clear
    echo "=========== CONEXION WIFI ADB ==========="
    read -p "IP del dispositivo destino: " DEVICE_IP
    read -p "Puerto de emparejamiento: " PAIR_PORT
    read -p "Codigo de emparejamiento (6 digitos): " PAIR_CODE
    read -p "Puerto de conexion [5555]: " CONNECT_PORT
    CONNECT_PORT=${CONNECT_PORT:-5555}

    info "Emparejando..."
    adb pair $DEVICE_IP:$PAIR_PORT $PAIR_CODE >/dev/null 2>&1 || { warning "Fallo pair"; read -p "Enter..."; return 1; }
    info "Conectando..."
    adb connect $DEVICE_IP:$CONNECT_PORT >/dev/null 2>&1 || { warning "Fallo connect"; read -p "Enter..."; return 1; }
    sleep 1
    if adb devices | grep -w "$DEVICE_IP:$CONNECT_PORT" >/dev/null; then
        CONNECTED=1
        success "Conectado"
    else
        warning "No conectado"
    fi
    read -p "Enter..."
}

inyectar() {
    clear
    echo "=========== INYECTANDO ==========="
    [ $CONNECTED -eq 0 ] && warning "Conectate primero" && read -p "Enter..." && return 1

    adb shell am force-stop $PACKAGE >/dev/null 2>&1
    adb shell "mkdir -p $DATA_PATH" >/dev/null 2>&1
    adb push "$REPO_PATH/." "$DATA_PATH/" >/dev/null 2>&1

    success "WALL INYECTADO CON EXITO"
    adb shell monkey -p $PACKAGE -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
    read -p "Presiona Enter para volver..."
}

bypass() {
    clear
    echo "=========== BYPASS ==========="
    [ $CONNECTED -eq 0 ] && warning "Conectate primero" && read -p "Enter..." && return 1
    if [ -f "$REPO_PATH/bypass.sh" ]; then
        adb push "$REPO_PATH/bypass.sh" /data/local/tmp/ >/dev/null 2>&1
        adb shell chmod +x /data/local/tmp/bypass.sh >/dev/null 2>&1
        adb shell sh /data/local/tmp/bypass.sh >/dev/null 2>&1
        success "Bypass ejecutado"
    else
        warning "No hay bypass.sh"
    fi
    read -p "Enter..."
}

desconectar() {
    [ $CONNECTED -eq 1 ] && adb disconnect $DEVICE_IP:$CONNECT_PORT >/dev/null 2>&1 && CONNECTED=0 && success "Desconectado" || warning "No conectado"
    read -p "Enter..."
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
    [ $CONNECTED -eq 1 ] && echo -e "[*] Estado: ${GREEN}CONECTADO${NC}" || echo -e "[*] Estado: ${RED}DESCONECTADO${NC}"
    echo
    echo "=========== MENU ==========="
    echo "1) CONECTAR"
    echo "2) INYECTAR"
    echo "3) BYPASS"
    echo "4) DESCONECTAR"
    echo "5) SALIR"
    echo "============================="
    read -p "Elegi una opcion: " op
    case $op in
        1) conectar ;;
        2) inyectar ;;
        3) bypass ;;
        4) desconectar ;;
        5) exit 0 ;;
        *) warning "Invalida"; sleep 1 ;;
    esac
    menu
}

check_deps
menu
