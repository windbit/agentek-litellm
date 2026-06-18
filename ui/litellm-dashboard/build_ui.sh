#!/bin/bash
set -e

# next 16 requires node >=20. Prefer an existing install (the Docker builder ships
# node via apk); only bootstrap nvm when node is missing or too old, since nvm
# needs curl, which the build image does not have.
need_nvm=1
if command -v node &> /dev/null; then
  node_major=$(node -p 'process.versions.node.split(".")[0]')
  if [ "$node_major" -ge 20 ]; then
    need_nvm=0
  fi
fi

if [ "$need_nvm" -eq 1 ]; then
  if ! command -v nvm &> /dev/null; then
    NVM_VERSION="v0.40.4"
    NVM_CHECKSUM="4b7412c49960c7d31e8df72da90c1fb5b8cccb419ac99537b737028d497aba4f"
    NVM_SCRIPT=$(mktemp)
    trap 'rm -f "$NVM_SCRIPT"' EXIT
    curl -fsSL "https://raw.githubusercontent.com/nvm-sh/nvm/${NVM_VERSION}/install.sh" -o "$NVM_SCRIPT"
    if command -v sha256sum &>/dev/null; then
      echo "${NVM_CHECKSUM}  ${NVM_SCRIPT}" | sha256sum -c -
    elif command -v shasum &>/dev/null; then
      echo "${NVM_CHECKSUM}  ${NVM_SCRIPT}" | shasum -a 256 -c -
    else
      echo "No sha256 tool found; cannot verify nvm checksum"; exit 1
    fi || { echo "nvm checksum verification failed"; exit 1; }
    bash "$NVM_SCRIPT"
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
  fi
  nvm install 20
  nvm use 20
fi

echo "Building admin UI with node $(node --version)"

# print contents of ui_colors.json
echo "Contents of ui_colors.json:"
cat ui_colors.json

# Install dependencies before building (node_modules is gitignored / absent in CI)
npm ci
npm run build

echo "Build successful. Copying files..."

destination_dir="../../litellm/proxy/_experimental/out"

# Recreate the destination (not committed, so it may be absent on a clean build)
rm -rf "$destination_dir"
mkdir -p "$destination_dir"

cp -r ./out/* "$destination_dir"
rm -rf ./out

echo "Deployment completed."
