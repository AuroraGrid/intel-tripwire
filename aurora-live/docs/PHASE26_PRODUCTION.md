# Phase 26 production release and operational qualification

Phase 26 supplies three supported deployment profiles:

- `public`: Docker Compose, PostgreSQL, worker, and Caddy-managed HTTPS.
- `cloud`: portable Kubernetes resources for a managed cloud or private
  cluster.
- `on_premises`: Docker Compose bound to localhost by default, with all
  storage under operator control.

None of the profiles requires an AI API. AURORA's deterministic correlation,
evidence, routing, and forecasting pipeline remains the default. External
model access is disabled unless an operator deliberately configures it.

## Public-first Docker deployment

1. Point a public DNS record to the host.
2. Copy `.env.production.example` to `.env`.
3. Replace every placeholder with a long random value.
4. Start:

   ```bash
   docker compose -f docker-compose.public.yml up --build -d
   ```

5. Confirm liveness and readiness through the HTTPS hostname.
6. Create the first administrator and preserve the returned token.
7. Record backup/restore, rollback, and disaster-recovery drills through the
   Phase 26 operations API.

Caddy obtains and renews the public TLS certificate. The application itself is
not exposed directly on the host.

## Cloud deployment

`deploy/phase26/kubernetes.yaml` is provider-neutral. Before applying it:

- replace `aurora.example.com`;
- replace the image reference with a published immutable image digest;
- replace the template Secret with a sealed or external secret;
- configure managed PostgreSQL;
- configure an Ingress controller and TLS issuer;
- verify network policy against the chosen database path.

The manifest includes two web replicas, one worker, readiness/liveness probes,
resource bounds, a disruption budget, horizontal autoscaling, and a restricted
container security context.

## On-premises deployment

```bash
docker compose -f docker-compose.onprem.yml up --build -d
```

The default binding is `127.0.0.1:8090`. Put an organization-controlled HTTPS
reverse proxy in front of it before making it reachable outside the host.
Mirror images and source packages locally for disconnected use.

## Qualification rules

Configuration is not evidence of uptime. Phase 26 stores workspace-scoped:

- operational samples;
- latency and freshness;
- backup/restore drills;
- rollback drills;
- disaster-recovery drills;
- load, security, and mobile acceptance records.

No samples means `NOT_VERIFIED`, never `PASS`. Public readiness requires a
measured 99.9% sample window, the required drills, and a passing Phase 25
integration record. Independent security review and sustained public history
remain external gates.

