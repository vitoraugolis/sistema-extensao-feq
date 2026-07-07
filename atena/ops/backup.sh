#!/bin/zsh
# Backup diário: banco (sqlite .backup consistente) + catálogo ontológico. Mantém 14.
cd "$(dirname "$0")/../.."
mkdir -p atena/backups
ts=$(date +%Y%m%d_%H%M)
[ -f atena/data/atena.db ] && sqlite3 atena/data/atena.db ".backup atena/backups/atena_$ts.db"
cp atena/ontologia/catalogo.jsonld "atena/backups/catalogo_$ts.jsonld"
ls -t atena/backups/atena_*.db 2>/dev/null | tail -n +15 | xargs rm -f
ls -t atena/backups/catalogo_*.jsonld 2>/dev/null | tail -n +15 | xargs rm -f
