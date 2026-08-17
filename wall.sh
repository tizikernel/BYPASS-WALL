#!/data/data/com.termux/files/usr/bin/bash

RED='\033[1;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;36m'
NC='\033[0m'

REPO_PATH="$HOME/BYPASS-WALL/com.dts.freefireth"
PACKAGE="com.dts.freefireth"
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
    if [ $CONNECTED -eq 1 ]; then
        warning "Ya conectado a $DEVICE_IP"
        return 0
    fi
    read -p "IP del dispositivo (ej. 192.168.1.10): " DEVICE_IP
    read -p "Puerto de emparejamiento (ej. 37000): " PAIR_PORT
    read -p "Puerto de conexion (ej. 5555, por defecto 5555): " CONNECT_PORT
    CONNECT_PORT=${CONNECT_PORT:-5555}
    read -p "Codigo de emparejamiento (6 digitos): " PAIR_CODE

    info "Emparejando con $DEVICE_IP:$PAIR_PORT ..."
    adb pair $DEVICE_IP:$PAIR_PORT $PAIR_CODE
    if [ $? -ne 0 ]; then
        warning "Fallo el emparejamiento. Verifica codigo y puerto."
        return 1
    fi

    info "Conectando a $DEVICE_IP:$CONNECT_PORT ..."
    adb connect $DEVICE_IP:$CONNECT_PORT
    sleep 1
    if adb devices | grep -w "$DEVICE_IP:$CONNECT_PORT" >/dev/null; then
        CONNECTED=1
        success "Conectado a $DEVICE_IP"
    else
        CONNECTED=0
        DEVICE_IP=""
        warning "Fallo la conexion"
        return 1
    fi
}

desconectar() {
    if [ $CONNECTED -eq 1 ]; then
        adb disconnect $DEVICE_IP:$CONNECT_PORT 2>/dev/null
        CONNECTED=0
        DEVICE_IP=""
        success "Desconectado"
    else
        warning "No hay conexion activa"
    fi
}

inyectar() {
    clear
    echo "=========== INYECTANDO ==========="
    [ $CONNECTED -eq 0 ] && warning "Conectate primero" && return 1
    info "Deteniendo $PACKAGE ..."
    adb shell am force-stop $PACKAGE
    info "Creando directorio..."
    adb shell "mkdir -p /data/data/$PACKAGE/files/"
    info "Copiando archivos..."
    adb push "$REPO_PATH/"* /data/data/$PACKAGE/files/ 2>/dev/null
    [ $? -eq 0 ] && success "Archivos copiados" || warning "Error en copia"
    adb shell chmod -R 755 "/data/data/$PACKAGE/files/"
    info "Abriendo Free Fire..."
    adb shell am start -n $PACKAGE/.SplashActivity 2>/dev/null
    [ $? -eq 0 ] && success "Free Fire iniciado" || warning "No se pudo abrir"
    read -p "Presiona Enter para volver..."
}

bypass() {
    clear
    echo "=========== BYPASS ==========="
    [ $CONNECTED -eq 0 ] && warning "Conectate primero" && return 1
    if [ -f "$REPO_PATH/bypass.sh" ]; then
        adb push "$REPO_PATH/bypass.sh" /data/local/tmp/
        adb shell chmod +x /data/local/tmp/bypass.sh
        adb shell sh /data/local/tmp/bypass.sh
        success "Bypass ejecutado"
    else
        warning "No hay bypass.sh"
    fi
    read -p "Presiona Enter para volver..."
}

reiniciar() {
    clear
    echo "=========== REINICIAR ==========="
    [ $CONNECTED -eq 0 ] && warning "Conectate primero" && return 1
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
    [ $CONNECTED -eq 1 ] && echo -e "[*] Estado: ${GREEN}CONECTADO${NC} a $DEVICE_IP" || echo -e "[*] Estado: ${RED}DESCONECTADO${NC}"
    echo
    echo "=========== MENU ==========="
    echo "1) CONECTAR (pair + connect)"
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
