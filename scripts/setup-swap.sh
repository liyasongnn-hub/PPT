#!/usr/bin/env bash
# 给 2GB 内存的轻量服务器加一块 swap 文件，防止生成任务瞬时内存峰值触发 OOM Killer。
#
# 用法（root）：
#   sudo bash scripts/setup-swap.sh [size_in_MB]
#
# 默认 2048MB，持久化写入 /etc/fstab（重启后仍生效），swap 优先级 10。
# 幂等：若已存在目标 swap 文件，则先关闭并销毁旧的再重建。

set -euo pipefail

SWAP_FILE="${SWAP_FILE:-/swapfile}"
SIZE_MB="${1:-2048}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "需要 root 权限，请用 sudo 运行" >&2
  exit 1
fi

# 销毁旧 swap（若存在）
if swapon --show | grep -q "^${SWAP_FILE}"; then
  echo "==> 关闭旧 swap：${SWAP_FILE}"
  swapoff "$SWAP_FILE" || true
fi
if [[ -f "$SWAP_FILE" ]]; then
  echo "==> 删除旧 swap 文件"
  rm -f "$SWAP_FILE"
fi

echo "==> 创建 ${SIZE_MB}MB swap 文件"
fallocate -l "${SIZE_MB}M" "$SWAP_FILE" 2>/dev/null \
  || dd if=/dev/zero of="$SWAP_FILE" bs=1M count="$SIZE_MB" status=progress
chmod 600 "$SWAP_FILE"
mkswap "$SWAP_FILE" >/dev/null
swapon "$SWAP_FILE"

# 持久化到 fstab（避免重复写入）
if ! grep -q "^${SWAP_FILE} " /etc/fstab; then
  echo "${SWAP_FILE} none swap sw,pri=10 0 0" >> /etc/fstab
fi

echo "==> 当前内存与 swap："
free -h
