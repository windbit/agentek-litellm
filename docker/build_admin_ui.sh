#!/bin/bash
set -e

# Build the admin dashboard into the static export the proxy serves
# (litellm/proxy/_experimental/out). The export is not committed, so it is built
# on every image; build_ui.sh installs node, runs the build, and copies it.

# Apply the enterprise theme when present; the default UI builds without it.
if [ -f "enterprise/enterprise_ui/enterprise_colors.json" ]; then
    echo "Applying enterprise Admin UI colors"
    cp enterprise/enterprise_ui/enterprise_colors.json ui/litellm-dashboard/ui_colors.json
fi

cd ui/litellm-dashboard
chmod +x ./build_ui.sh
./build_ui.sh
