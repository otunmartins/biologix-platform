# Milestone 2 handoff

## Workflow

Creating an experiment places a job on the Redis `biologix` queue. An RQ worker loads the experiment from PostgreSQL and runs four scientific stages:

1. Resolve the biologic name to its known Protein Data Bank identifier.
2. Resolve and validate the polymer PSMILES repeat unit with RDKit.
3. Screen the representative repeat unit against the toxicity SMARTS library.
4. Check excipient precedent, jurisdictions, immunogenicity, and aggregation alerts.

Progress, stage events, timestamps, failures, and final structured results are written to PostgreSQL. The experiment page polls every two seconds while it is open. Failed jobs expose an error and can be submitted again.

## Local operation

```bash
cp .env.example .env
docker compose up --build postgres redis api worker frontend
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
```

## AWS ECS deployment

The Terraform module in `deploy/aws` deploys API, worker, and frontend services to an existing ECS Fargate cluster. It expects private subnets, application security groups, API and frontend target groups, and AWS Secrets Manager entries containing the RDS PostgreSQL URL, ElastiCache Redis URL, and application session secret.

The Application Load Balancer should route `/api/*` to the API target group and all other paths to the frontend target group. Build the frontend image with its public application URL as `API_URL`. The browser normally reaches `/api` through the load balancer rule.

Store each production value as a plain secret value in Secrets Manager. Grant human access narrowly. The Terraform execution role grants the ECS agent access only to the three secret ARNs supplied to the module.

Build and publish both images to ECR:

```bash
docker build -f Dockerfile.api -t "$ECR_API_IMAGE" .
docker build --build-arg API_URL=https://app.example.com -t "$ECR_WEB_IMAGE" frontend
docker push "$ECR_API_IMAGE"
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

RQ retains successful job metadata for one day and failed metadata for seven days. PostgreSQL remains the authoritative experiment record. Scale workers independently from the API. A worker termination during a stage leaves the experiment in `running`; operational monitoring should alert on stale running jobs until retry and recovery automation is added.

The current workflow is CPU suitable. OpenMM GPU execution remains a separate expansion because the API image does not contain the full CUDA simulation environment.
