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
    adb pair $DEVICE_IP:$PAIR_PORT $PAIR_CODE || { warning "Fallo pair"; read -p "Enter..."; return 1; }
    info "Conectando..."
    adb connect $DEVICE_IP:$CONNECT_PORT || { warning "Fallo connect"; read -p "Enter..."; return 1; }
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

    info "Deteniendo $PACKAGE..."
    adb shell am force-stop $PACKAGE

    info "Borrando carpeta vieja en $DATA_PATH ..."
    adb shell "rm -rf $DATA_PATH" 2>/dev/null

    info "Copiando $REPO_PATH a $DATA_PATH ..."
    adb shell "mkdir -p $DATA_PATH"
    adb push "$REPO_PATH/"* "$DATA_PATH/" 2>/dev/null

    if [ $? -eq 0 ]; then
        success "Inyeccion completada en $DATA_PATH"
    else
        warning "Error al copiar archivos"
        read -p "Enter..."
        return 1
    fi

    info "Abriendo Free Fire..."
    adb shell monkey -p $PACKAGE -c android.intent.category.LAUNCHER 1 2>/dev/null
    success "Free Fire iniciado (monkey)"
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
    [ $CONNECTED -eq 1 ] && echo -e "[*] Estado: ${GREEN}CONECTADO${NC} a $DEVICE_IP" || echo -e "[*] Estado: ${RED}DESCONECTADO${NC}"
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
        3) echo "Bypass no implementado"; sleep 1 ;;
        4) [ $CONNECTED -eq 1 ] && adb reboot && success "Reinicio enviado" || warning "Conectate primero"; sleep 1 ;;
        5) desconectar ;;
        6) exit 0 ;;
        *) warning "Invalida"; sleep 1 ;;
    esac
    menu
}

desconectar() {
    [ $CONNECTED -eq 1 ] && adb disconnect $DEVICE_IP:$CONNECT_PORT && CONNECTED=0 && success "Desconectado" || warning "No conectado"
    sleep 1
}

check_deps
menu
