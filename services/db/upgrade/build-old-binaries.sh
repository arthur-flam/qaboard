#!/bin/sh
# Runs while building the image (see Dockerfile): installs the old PostgreSQL, copied from its image to /opt/old, in
# /usr/local/pg$OLD_MAJOR, with the shared libraries it needs that the new image doesn't have.
set -eu
old=/usr/local/pg$OLD_MAJOR
old_root=/tmp/old-root  # the old image's /lib and /usr/lib

mkdir -p "$old/compat"
mv /opt/old/bin /opt/old/lib /opt/old/share "$old/"
# No LLVM: JIT only speeds up big queries, pg_upgrade doesn't need it (PostgreSQL disables JIT without llvmjit.so)
rm -rf "$old/lib/postgresql/llvmjit"* "$old/lib/postgresql/bitcode" "$old/lib/"*.a "$old/lib/pkgconfig"
# The loader searches compat/ last: the new binaries never load the old libraries
printf '/lib:/usr/local/lib:/usr/lib:%s/compat\n' "$old" > "/etc/ld-musl-$(uname -m).path"

elf_files() { find "$old/bin" "$old/lib" "$old/compat" -type f \( -perm -u+x -o -name '*.so*' \); }
missing_libs() { ldd "$1" 2>&1 | sed -n 's/^Error loading shared library \([^:]*\): .*/\1/p'; }

for _ in 1 2 3 4 5; do
  missing=$(elf_files | while read -r f; do missing_libs "$f"; done | sort -u)
  [ -z "$missing" ] && break
  copied=0
  for lib in $missing; do
    src=$(find "$old_root/lib" "$old_root/usr/lib" -maxdepth 1 -name "$lib" | head -1)
    if [ -n "$src" ]; then
      echo "compat: $lib"
      cp -L "$src" "$old/compat/"
      copied=1
    fi
  done
  [ "$copied" = 1 ] || break
done

# Modules whose libraries aren't in the old image either: the procedural languages (plperl needs perl, plpython
# python...). The old server never loads them during pg_upgrade (it only dumps the schema); the new server has its own.
for f in $(elf_files); do
  if [ -n "$(missing_libs "$f")" ]; then
    case "$f" in
      "$old/lib/postgresql/"*) echo "removed (needs $(missing_libs "$f" | tr '\n' ' ')): $f"; rm -f "$f";;
      *) echo "ERROR: $f needs $(missing_libs "$f" | tr '\n' ' ')" >&2; exit 1;;
    esac
  fi
done
rm -rf "$old_root" /opt/old

"$old/bin/postgres" --version | grep "(PostgreSQL) $OLD_MAJOR\."
"$old/bin/pg_ctl" --version
"$old/bin/pg_controldata" --version
postgres --version
pg_upgrade --version
