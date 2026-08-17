#!/data/data/com.termux/files/usr/bin/bash

# ======================================================
#  WALL ANDROID PRIVATE - INYECTOR REMOTO v3
#  BY UNKNOWN TEAM
# ======================================================

# Colores
RED='\033[1;31m'
GREEN='\033[1;32m'
YELLOW='\033[1;33m'
BLUE='\033[1;36m'
NC='\033[0m'

# Variables globales
REPO_PATH="$HOME/BYPASS-WALL/com.dts.freefireth"
PACKAGE="com.dts.freefireth"
ADB_PORT=5555
DEVICE_IP=""
CONNECTED=0

error() {
    echo -e "${RED}[ERROR] $1${NC}"
    exit 1
}

info() {
    echo -e "${BLUE}[*] $1${NC}"
}

success() {
    echo -e "${GREEN}[✔] $1${NC}"
}

warning() {
    echo -e "${YELLOW}[!] $1${NC}"
}

check_deps() {
    command -v adb >/dev/null 2>&1 || error "ADB no instalado. Ejecuta: pkg install android-tools"
    [ -d "$REPO_PATH" ] || error "No se encuentra el repositorio en $REPO_PATH. Asegúrate de tener clonado y copiado los archivos."
}

conectar() {
    if [ -z "$DEVICE_IP" ]; then
        read -p "Introduce la IP del dispositivo destino (ej. 192.168.1.10): " DEVICE_IP
    fi
    info "Conectando a $DEVICE_IP:$ADB_PORT ..."
    adb connect "$DEVICE_IP:$ADB_PORT" 2>/dev/null
    if [ $? -ne 0 ]; then
        warning "No se pudo conectar. Verifica que la depuración inalámbrica esté activa en el destino."
        CONNECTED=0
        return 1
    fi
    adb devices | grep -w "$DEVICE_IP:$ADB_PORT" >/dev/null
    if [ $? -ne 0 ]; then
        warning "Dispositivo no autorizado. Acepta la conexión en el destino."
        CONNECTED=0
        return 1
    fi
    CONNECTED=1
    success "Conectado a $DEVICE_IP"
    return 0
}

inyectar() {
    clear
    echo "=========== INYECTANDO ==========="
    conectar || return 1

    info "Deteniendo Free Fire en el destino..."
    adb shell am force-stop $PACKAGE

    info "Creando directorio de datos en el destino..."
    adb shell "mkdir -p /data/data/$PACKAGE/files/"

    info "Copiando archivos de bypass desde $REPO_PATH a /data/data/$PACKAGE/files/ ..."
    adb push "$REPO_PATH/"* /data/data/$PACKAGE/files/ 2>/dev/null
    if [ $? -ne 0 ]; then
        warning "Error al copiar algunos archivos. Puede que algunos ya existan."
    else
        success "Archivos copiados correctamente."
    fi

    info "Estableciendo permisos..."
    adb shell chmod -R 755 "/data/data/$PACKAGE/files/"

    info "Abriendo Free Fire automáticamente..."
    adb shell am start -n $PACKAGE/.SplashActivity 2>/dev/null
    if [ $? -eq 0 ]; then
        success "Free Fire iniciado en el destino."
    else
        warning "No se pudo iniciar Free Fire. Inícialo manualmente."
    fi

    echo "===================================="
    read -p "Presiona Enter para volver al menú..."
}

bypass() {
    clear
    echo "=========== BYPASS ==========="
    conectar || return 1

    if [ -f "$REPO_PATH/bypass.sh" ]; then
        info "Subiendo y ejecutando bypass.sh ..."
        adb push "$REPO_PATH/bypass.sh" /data/local/tmp/
        adb shell chmod +x /data/local/tmp/bypass.sh
        adb shell sh /data/local/tmp/bypass.sh
        success "Bypass ejecutado."
    else
        warning "No se encontró bypass.sh. Ejecutando comando genérico..."
        adb shell settings put global hidden_api_policy 1
        success "Comando genérico aplicado."
    fi

    echo "===================================="
    read -p "Presiona Enter para volver al menú..."
}

reiniciar() {
    clear
    echo "=========== REINICIAR DISPOSITIVO ==========="
    conectar || return 1
    info "Reiniciando el dispositivo destino..."
    adb reboot
    success "Reinicio enviado. El dispositivo se apagará y encenderá."
    CONNECTED=0
    DEVICE_IP=""
    echo "===================================="
    read -p "Presiona Enter para volver al menú..."
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
    if [ $CONNECTED -eq 1 ] && [ ! -z "$DEVICE_IP" ]; then
        echo -e "[*] Estado: ${GREEN}CONECTADO${NC} a $DEVICE_IP"
    else
        echo -e "[*] Estado: ${RED}DESCONECTADO${NC}"
    fi
    echo
    echo "=========== MENU ==========="
    echo "1) INYECTAR (copiar archivos y abrir FF)"
    echo "2) BYPASS (ejecutar script extra)"
    echo "3) REINICIAR DISPOSITIVO"
    echo "4) SALIR"
    echo "============================="
    echo
    read -p "Elegí una opción: " op

    case "$op" in
        1) inyectar ;;
        2) bypass ;;
        3) reiniciar ;;
        4) echo "Saliendo..."; exit 0 ;;
        *) warning "Opción inválida"; sleep 1; menu ;;
    esac
    menu
}

check_deps
menu
