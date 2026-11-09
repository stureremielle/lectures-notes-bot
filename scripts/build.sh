#!/bin/sh
# Build the image on the box and show what is there to start with.
#
#   ./scripts/build.sh            # normal build (BuildKit)
#   ./scripts/build.sh --legacy   # classic builder, no buildkit cache dirs
#   ./scripts/build.sh --no-cache # full rebuild
#
# BuildKit keeps its cache in $DOCKER_ROOT/buildkit/containerd-overlayfs. If
# that directory is missing (external drive not mounted, or lost in a power
# cut) the build dies with "stat .../cachemounts: no such file or directory".
# --legacy sidesteps it; the preflight below tells you which case you are in.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker

LEGACY=0
NOCACHE=0
for arg in "$@"; do
  case "$arg" in
    --legacy) LEGACY=1 ;;
    --no-cache) NOCACHE=1 ;;
    --help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die "opsi tidak dikenal: $arg (pakai: --legacy | --no-cache)" ;;
  esac
done

say "Docker: $(docker version --format '{{.Server.Version}}') / arsitektur $(uname -m)"

# --- preflight: is the docker data root on a healthy, writable mount? -------
ROOTDIR=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
if [ -n "$ROOTDIR" ]; then
  say "Docker data root: $ROOTDIR"
  if ! ls "$ROOTDIR" >/dev/null 2>&1; then
    die "tidak bisa membaca $ROOTDIR — drive belum ter-mount? cek: findmnt -T $ROOTDIR"
  fi
  MP=$(findmnt -n -o TARGET -T "$ROOTDIR" 2>/dev/null || echo '?')
  if [ "$MP" = "/" ] && [ "$ROOTDIR" != "/var/lib/docker" ]; then
    die "data root $ROOTDIR ada di filesystem root, bukan di mount drive.
    Drive eksternal kemungkinan belum ter-mount, sehingga docker memakai
    direktori kosong. Cek: findmnt /extdrive/ext1 ; sudo mount -a ;
    lalu: sudo systemctl restart docker"
  fi
  if ! touch "$ROOTDIR/.lnb-write-test" 2>/dev/null; then
    die "$ROOTDIR tidak bisa ditulis (filesystem read-only / rusak?).
    Cek: mount | grep -E 'ext4|extdrive'  dan  dmesg | grep -i -E 'EXT4-fs error|remount'"
  fi
  rm -f "$ROOTDIR/.lnb-write-test"
  ok_dir="$ROOTDIR/buildkit"
  if [ "$LEGACY" = "0" ] && [ ! -d "$ok_dir" ]; then
    warn "BuildKit cache tidak ada di $ok_dir (sisa power cut / data root baru)."
    warn "Biasanya cukup: sudo systemctl restart docker  lalu build lagi."
    warn "Kalau masih gagal: sudo rm -rf $ok_dir"
    warn "Catatan: '--legacy' hanya bekerja tanpa containerd image store;"
    warn "setup ini memakai containerd snapshots, jadi tetap pakai builder baru."
  fi
fi

BUILD_ARGS="-t $IMAGE ."
[ "$NOCACHE" = "1" ] && BUILD_ARGS="--no-cache $BUILD_ARGS"

say "Build image $IMAGE (aarch64 native, tanpa ffmpeg — caption saja)"
if [ "$LEGACY" = "1" ]; then
  say "builder: klasik (DOCKER_BUILDKIT=0)"
  # shellcheck disable=SC2086
  if ! DOCKER_BUILDKIT=0 docker build $BUILD_ARGS; then
    warn "build klasik gagal juga. Kalau pesannya soal cachemounts/buildkit:"
    printf '    sudo rm -rf %s/buildkit   # cache saja, image tidak terhapus\n' "${ROOTDIR:-<root>}"
    exit 1
  fi
else
  if ! docker build $BUILD_ARGS; then
    warn "build gagal. Bila error menyebut buildkit/cachemounts:"
    printf '    sudo rm -rf %s/buildkit   # atau: ./scripts/build.sh --legacy\n' "${ROOTDIR:-<root>}"
    exit 1
  fi
fi

say "Image lokal:"
docker images --format '  {{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}' | grep "lecture-notes-bot" || true

say "Langkah berikutnya:"
printf '  1) buat container (Portainer: Stacks -> Update the stack)\n'
printf '     atau tanpa Portainer: ./scripts/recreate.sh\n'
printf '  2) cek: ./scripts/doctor.sh\n'
