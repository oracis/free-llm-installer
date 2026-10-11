@echo off
rem ccproxy vision instance: 127.0.0.1:3458 -> Kilo gateway omni (free, keyless, vision + tools)
python "%~dp0ccproxy.py" --port 3458 --upstream https://api.kilo.ai/api/gateway/v1/chat/completions --model nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free --key anonymous
