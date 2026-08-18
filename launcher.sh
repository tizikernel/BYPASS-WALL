#!/data/data/com.termux/files/usr/bin/bash

ARCH=$(uname -m)

case $ARCH in
  aarch64)
    ./wall_arm64
    ;;
  armv7l)
    echo "ADVERTENCIA: Dispositivo ARMv7 (32 bits) detectado."
    echo "Ejecutando script ofuscado (requiere Python)..."
    python wall_ofuscado.py
    ;;
  *)
    echo "ERROR: Arquitectura no soportada: $ARCH"
    exit 1
    ;;
esac
