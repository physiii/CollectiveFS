#!/usr/bin/env bash
set -euo pipefail
cfs_lib_root=$(cd "$(dirname "$0")/.." && pwd)
cfs_ndk=${NDK_HOME:?Set NDK_HOME to Android NDK r28 or newer}
cfs_output=${1:?Pass the jniLibs output directory}
cfs_arch=${CFS_ARCHES:-arm64-v8a armeabi-v7a x86_64}
cfs_toolchain="$cfs_ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
cd "$cfs_lib_root"
for cfs_abi in $cfs_arch; do
 case "$cfs_abi" in
 arm64-v8a) cfs_goarch=arm64; cfs_cc=aarch64-linux-android24-clang ;;
 armeabi-v7a) cfs_goarch=arm; cfs_cc=armv7a-linux-androideabi24-clang ;;
 x86_64) cfs_goarch=amd64; cfs_cc=x86_64-linux-android24-clang ;;
 *) echo "Unsupported ABI: $cfs_abi" >&2; exit 1 ;;
 esac
 mkdir -p "$cfs_output/$cfs_abi"
 CGO_ENABLED=1 GOOS=android GOARCH="$cfs_goarch" GOARM=7 CC="$cfs_toolchain/$cfs_cc" \
 go build -trimpath -buildmode=c-shared -ldflags='-s -w -extldflags=-Wl,-z,max-page-size=16384' -o "$cfs_output/$cfs_abi/libcollectivefs.so" ./cmd/android-node
 rm -f "$cfs_output/$cfs_abi/libcollectivefs.h"
done
