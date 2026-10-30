# Keeps the packaged SDK identical to the copy the engine serves.
Copy-Item "$PSScriptRoot\..\apps\console\static\tynode_sdk.py" "$PSScriptRoot\..\packages\sdk-python\tynode_sdk.py" -Force