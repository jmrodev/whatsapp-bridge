#!/usr/bin/env bash
set -e
INSTALL_DIR="${HOME}/.local/bin"
SCRIPT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/whatsapp_cli.py"

mkdir -p "${INSTALL_DIR}"
cat << WRAPPEREOF > "${INSTALL_DIR}/whatsapp"
#!/usr/bin/env bash
python3 "${SCRIPT_SRC}" "\$@"
WRAPPEREOF

chmod +x "${INSTALL_DIR}/whatsapp"
chmod +x "${SCRIPT_SRC}"
echo "✅ Comando 'whatsapp' instalado en ${INSTALL_DIR}/whatsapp"
