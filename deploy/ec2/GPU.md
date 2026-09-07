# GPU deployment

The scientific worker image already ships the OpenMM CUDA plugin (`libOpenMMCUDA.so`).
On a CPU host OpenMM reports only `[Reference, CPU]`; give the host an NVIDIA GPU and
it uses `CUDA` automatically. No changes to the science code are required.

## What is wired

- `docker-compose.gpu.yml` — grants the worker container GPU access and sets
  `BIOLOGIX_AI_OPENMM_PLATFORM=CUDA`.
- `cloudformation.yml` — `InstanceType` now accepts `g4dn.xlarge` and `g5.xlarge`.
  On boot the UserData detects an NVIDIA GPU (`lspci`), installs the driver +
  `nvidia-container-toolkit` if missing, wires the Docker NVIDIA runtime, and brings the
  stack up with the GPU override. On a non-GPU instance this block is skipped and the
  deploy is byte-for-byte the CPU path.
- The GPU setup is best-effort: if it fails, the stack still comes up on CPU rather than
  aborting. So a GPU launch never leaves you with a dead box.

## Prerequisites (AWS account side)

1. The account must be on a **paid plan** (the free plan cannot launch G instances).
2. Request a service-quota increase for **Running On-Demand G and VT instances**
   (new accounts start at 0 vCPUs).
3. Cost: `g4dn.xlarge` ≈ $0.53/hr (~$384/mo if left on). Consider running the GPU host
   only when heavy simulation is needed.

## Launch

```bash
aws cloudformation deploy \
  --region us-east-1 \
  --stack-name biologix-production \
  --template-file deploy/ec2/cloudformation.yml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides AdminEmails=you@example.com InstanceType=g4dn.xlarge
```

Most reliable path: launch on an AMI that already has the NVIDIA driver and container
toolkit (AWS Deep Learning Base AMI, or the NVIDIA GPU-Optimized AMI). Pass its image id
via the `LatestAmiId` parameter. Then UserData only adds the compose override and skips
the driver install entirely.

## Verify GPU is in use

```bash
# on the host
nvidia-smi
cd /opt/biologix/app/deploy/ec2
docker compose exec -T worker bash -lc \
  'source /opt/conda/etc/profile.d/conda.sh; conda activate biologix-ai-sim; \
   python -c "import openmm; print([openmm.Platform.getPlatform(i).getName() for i in range(openmm.Platform.getNumPlatforms())])"'
# expect the list to include CUDA
```

The worker's `openmm_platform` in an experiment's result `capabilities` will read `CUDA`.

## Not yet validated on real GPU hardware

The wiring is standard and the templates parse, but it has not been run on an actual GPU
instance (the current account is on the free plan). Validate the driver bootstrap on the
first real GPU launch; if the stock-AMI driver install misbehaves, use the GPU-driver AMI
path above, which sidesteps it.
