# Milestone 2 handoff

## Workflow

Creating an experiment places a job on the Redis `biologix` queue. An RQ worker loads the experiment from PostgreSQL and runs these scientific stages:

1. Resolve the biologic name to its known Protein Data Bank identifier.
2. Resolve and validate the polymer PSMILES repeat unit with RDKit.
3. Screen the representative repeat unit against the toxicity SMARTS library.
4. Check excipient precedent, jurisdictions, immunogenicity, and aggregation alerts.
5. Plan retrosynthesis routes when RetroSynthesisAgent is available.
6. Run molecular physics when OpenMM and Packmol are available.
7. Generate a PDF report, structured results, and an immutable audit trail.

Progress, immutable stage events, timestamps, failures, and final structured results are written to PostgreSQL. Reports and audit files are stored in MinIO locally and S3 in AWS. The experiment page polls every two seconds while it is open. Failed jobs expose an error and can be submitted again.

## Local operation

```bash
cp .env.example .env
docker compose up --build postgres redis minio api worker frontend
```

Open `http://localhost:3000`, create an account, and submit an experiment. `Human insulin` with `PEG` exercises the complete supported path.

When running the worker directly on macOS, use the nonforking worker class because RDKit can conflict with the Objective C runtime after a process fork:

```bash
rq worker biologix --url redis://localhost:6379/0 --worker-class rq.worker.SimpleWorker
```

Useful operations:

```bash
docker compose logs -f worker
docker compose exec redis redis-cli LLEN rq:queue:biologix
docker compose exec api alembic current
docker compose up --scale worker=3
docker compose exec worker python scripts/platform_healthcheck.py
```

## AWS ECS deployment

The Terraform module in `deploy/aws` deploys API, worker, and frontend services to an existing ECS Fargate cluster. It also creates a private encrypted and versioned S3 bucket for experiment artifacts. It expects private subnets, application security groups, API and frontend target groups, and AWS Secrets Manager entries containing the RDS PostgreSQL URL, ElastiCache Redis URL, and application session secret.

The Application Load Balancer should route `/api/*` to the API target group and all other paths to the frontend target group. Build the frontend image with its public application URL as `API_URL`. The browser normally reaches `/api` through the load balancer rule.

Store each production value as a plain secret value in Secrets Manager. Grant human access narrowly. The Terraform execution role grants the ECS agent access only to the three secret ARNs supplied to the module.

Build and publish both images to ECR:

```bash
docker build -f Dockerfile.api -t "$ECR_API_IMAGE" .
docker build --build-arg SLIM=1 -t "$ECR_WORKER_IMAGE" .
docker build --build-arg API_URL=https://app.example.com -t "$ECR_WEB_IMAGE" frontend
docker push "$ECR_API_IMAGE"
docker push "$ECR_WORKER_IMAGE"
docker push "$ECR_WEB_IMAGE"
```

Copy `deploy/aws/terraform.tfvars.example` to `terraform.tfvars`, replace every example value, and deploy:

```bash
cd deploy/aws
terraform init
terraform validate
terraform plan
terraform apply
```

Use RDS PostgreSQL 16 or newer and ElastiCache Redis 7 or newer. Keep both in private subnets. Permit port 5432 and port 6379 only from the ECS task security group. Terminate HTTPS at the load balancer and set the application cookie as secure.

Run the post deployment check after DNS and TLS are ready:

```bash
scripts/aws_smoke_test.sh https://app.example.com
```

The check creates an isolated account, submits an insulin and PEG experiment, waits for the worker, and verifies the completed scientific result.

## Operational notes

RQ retains successful job metadata for one day and failed metadata for seven days. PostgreSQL remains the authoritative experiment record. Scale workers independently from the API. Worker startup requeues experiments that remained in the running state beyond `STALE_JOB_MINUTES`. The default is 45 minutes.

The worker selects CUDA, OpenCL, CPU, or Reference in that order when automatic platform selection is enabled. CPU is the normal fallback. The API image stays small and does not contain the scientific environment.

## Verification

Run the focused platform suite and frontend build:

```bash
pytest tests/platform
cd frontend && npm run build
```

Run the complete scientific suite in the conda environment:

```bash
mamba run -n biologix-ai-sim pytest
```

The structured result payload records capability availability and the status of every optional stage. An unavailable optional dependency is reported as unavailable and is never presented as a completed scientific calculation.
