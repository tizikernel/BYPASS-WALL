#!/data/data/com.termux/files/usr/bin/bash
ARCH=$(uname -m)
case $ARCH in
  aarch64) ./wall_arm64 ;;
  armv7l)  echo "Error: No hay binario para ARMv7. Ejecuta: python wall" ;;
  *)       echo "Arquitectura no soportada: $ARCH"; exit 1 ;;
esac
