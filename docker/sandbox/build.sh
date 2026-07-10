#!/bin/bash
# 鏋勫缓娌欑闀滃儚

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="lanjian/sandbox"
IMAGE_TAG="latest"

echo "Building sandbox image: ${IMAGE_NAME}:${IMAGE_TAG}"

docker build \
    -t "${IMAGE_NAME}:${IMAGE_TAG}" \
    -f "${SCRIPT_DIR}/Dockerfile" \
    "${SCRIPT_DIR}"

echo "Build complete: ${IMAGE_NAME}:${IMAGE_TAG}"

# 楠岃瘉闀滃儚
echo "Verifying image..."
docker run --rm "${IMAGE_NAME}:${IMAGE_TAG}" python3 --version
docker run --rm "${IMAGE_NAME}:${IMAGE_TAG}" node --version

echo "Sandbox image ready!"

