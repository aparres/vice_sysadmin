#!/usr/bin/env bash
# Ejecutar desde la raíz del proyecto Linux. Sin API ni experimento.
set -euo pipefail
printf '\n--- YAML real t2-3 ---\n'
cat tasks/t2-3-fix-sudoers-entry.yaml
printf '\n--- YAML real t5-2 ---\n'
cat tasks/t5-2-free-disk-space.yaml
printf '\n--- Disco del host ---\n'
df --output=size -BM /
total_mb=$(df --output=size -BM / | tail -1 | tr -dc '0-9')
printf 'host total_mb=%s\n' "$total_mb"
if [ "$total_mb" -le 8192 ]; then echo 'host <=8192: SI'; else echo 'host <=8192: NO'; fi

probe="vice-prereq-$(date +%s)-$$"
created=0
cleanup() {
    if [ "$created" -eq 1 ]; then
        lxc delete "$probe" --force || {
            printf 'No se pudo eliminar el contenedor de diagnóstico: %s\n' "$probe" >&2
            return 1
        }
    fi
}
trap cleanup EXIT
# Mismo copy de snapshot y perfil que LxdRuntime.create.
lxc copy vice-template/base "$probe" --profile vicesysadmin
created=1
lxc start "$probe"
lxc exec "$probe" -- bash -s <<'CONTAINER'
set -eu
printf '\n--- Versión de sudo y locale ---\n'
sudo --version
locale
printf '\n--- Setup exacto t2-3 ---\n'
id deploy_bot >/dev/null 2>&1 || useradd -m -s /bin/bash deploy_bot
printf '#!/bin/bash\necho deploying\n' > /usr/local/bin/deploy.sh
chmod 755 /usr/local/bin/deploy.sh
echo 'deploy_bot ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/deploy_bot
chmod 440 /etc/sudoers.d/deploy_bot
visudo -c >/dev/null

diagnose() {
    # Guardar stdout sin editarlo y abortar si sudo falla, antes de evaluar grep.
    sudo -l -U deploy_bot > /tmp/vice-prereq-sudo-list.txt
    cat /tmp/vice-prereq-sudo-list.txt
    if grep -qE 'NOPASSWD:[[:space:]]*ALL[[:space:]]*$' /tmp/vice-prereq-sudo-list.txt; then
        echo 'Regex: MATCH'
    else
        rc=$?
        [ "$rc" -eq 1 ] || exit "$rc"
        echo 'Regex: NO MATCH'
    fi
    # Check original, sin modificar.
    if ! sudo -l -U deploy_bot | grep -qE 'NOPASSWD:[[:space:]]*ALL[[:space:]]*$'; then
        echo 'no_blanket_nopasswd: PASS'
    else
        echo 'no_blanket_nopasswd: FAIL'
    fi
}
printf '\n--- ANTES ---\n'
diagnose
printf '\n--- Fix esperado, solo en clon descartable ---\n'
echo 'deploy_bot ALL=(ALL) NOPASSWD: /usr/local/bin/deploy.sh' > /etc/sudoers.d/deploy_bot
chmod 440 /etc/sudoers.d/deploy_bot
visudo -c
printf '\n--- DESPUÉS ---\n'
diagnose
if su - deploy_bot -c 'sudo -n /usr/local/bin/deploy.sh' | grep -q deploying; then
    echo 'deploy_script_allowed: PASS'
else
    echo 'deploy_script_allowed: FAIL'
fi
if ! su - deploy_bot -c 'sudo -n /usr/bin/whoami' >/dev/null 2>&1; then
    echo 'arbitrary_command_denied: PASS'
else
    echo 'arbitrary_command_denied: FAIL'
fi
printf '\n--- Disco del contenedor (contexto real del setup t5-2) ---\n'
df --output=size -BM /
total_mb=$(df --output=size -BM / | tail -1 | tr -dc '0-9')
printf 'container total_mb=%s\n' "$total_mb"
if [ "$total_mb" -le 8192 ]; then
    echo 't5-2 requisito tamaño total: PASS'
else
    echo 't5-2 requisito tamaño total: FAIL'
fi
# No ejecutar setup t5-2: no crear archivos de relleno.
CONTAINER
