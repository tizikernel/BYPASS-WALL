# WALL PRIVATE BY UNKNOWN TEAM

El archivo `wall` es el script Python basado directamente en `Ribeiro00.py`. Se conservaron su estructura, funciones, comandos, rutas y lógica original. Solo se tradujeron los textos visibles al español, se eliminaron los emojis y se cambió el encabezado a `WALL PRIVATE / BY UNKNOWN TEAM`.

## Ejecución en Termux

```bash
pkg install -y python android-tools
chmod +x wall
./wall
```

También puede ejecutarse explícitamente con Python:

```bash
python3 wall
```

Para comprobar los dispositivos ADB:

```bash
./wall devices
```

El script conserva la preparación automática del entorno de Termux definida en el archivo original. Si se inicia sin argumentos, abre el menú interactivo. Los subcomandos disponibles son `devices`, `send`, `check-status`, `check-ffrtc` y `burla-ffrtc`.
