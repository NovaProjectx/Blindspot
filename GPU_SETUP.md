# GPU setup for Blindspot

The VPS host can show an NVIDIA GPU with `nvidia-smi`, but Docker also needs NVIDIA Container Toolkit/CDI support before containers can use CUDA.

Check host GPU:

```bash
nvidia-smi
```

Check Docker GPU support:

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04 nvidia-smi
```

If that fails, install/configure NVIDIA Container Toolkit as root/sudo:

```bash
bash scripts/install_nvidia_container_toolkit.sh
```

Then restart Blindspot with GPU override:

```bash
cd /home/azureuser/Blindspot
docker-compose down
docker-compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
curl http://127.0.0.1:8080/status
```

If GPU setup is not available, the app still works in CPU fallback mode, but it displays:

```text
CPU mode is for testing, not high-level protection.
```
