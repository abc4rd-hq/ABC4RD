#!/usr/bin/env bash

set -euo pipefail

minimum_cpu=4
minimum_memory_kib=$((8 * 1024 * 1024))
minimum_free_disk_kib=$((60 * 1024 * 1024))
install_root="${ABC4RD_INSTALL_ROOT:-/opt/abc4rd}"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "ОШИБКА: установка рассчитана на отдельный Linux-сервер."
  exit 1
fi

cpu_count="$(getconf _NPROCESSORS_ONLN)"
memory_kib="$(awk '/^MemTotal:/ { print $2 }' /proc/meminfo)"

disk_probe="$install_root"
while [[ ! -e "$disk_probe" && "$disk_probe" != "/" ]]; do
  disk_probe="$(dirname "$disk_probe")"
done
free_disk_kib="$(df -Pk "$disk_probe" | awk 'NR == 2 { print $4 }')"

failures=0

echo "ABC4RD Open edX — проверка нового сервера"
echo "CPU: ${cpu_count} vCPU, требуется не менее ${minimum_cpu}"
echo "RAM: $((memory_kib / 1024 / 1024)) ГБ, требуется не менее 8 ГБ"
echo "Свободный диск: $((free_disk_kib / 1024 / 1024)) ГБ, требуется не менее 60 ГБ"

if (( cpu_count < minimum_cpu )); then
  echo "НЕ ПРОЙДЕНО: недостаточно процессорных ядер."
  failures=$((failures + 1))
fi

if (( memory_kib < minimum_memory_kib )); then
  echo "НЕ ПРОЙДЕНО: недостаточно оперативной памяти."
  failures=$((failures + 1))
fi

if (( free_disk_kib < minimum_free_disk_kib )); then
  echo "НЕ ПРОЙДЕНО: недостаточно свободного диска."
  failures=$((failures + 1))
fi

if (( failures > 0 )); then
  echo "ИТОГ: СЕРВЕР НЕ ПОДХОДИТ. Установка остановлена до устранения проблем."
  exit 1
fi

echo "ИТОГ: ГОТОВ К УСТАНОВКЕ."
