# Security model and deployment limits

This is a development beta, not an independently audited hosting platform. Default access requires an approved role.
Docker containers are not full VMs and share the host kernel. Do not expose this beta to arbitrary hostile tenants.

## Secrets

Reset the Discord token and revoke the Freestyle key that were disclosed in chat. Neither value is present in this archive.
The runtime token lives in the private `.env`, not Python source or a Git remote. Tenant containers receive only TERM/LANG,
no bot/provider credentials, host bind mounts, Docker socket, devices or host namespaces.
Freestyle credentials are used locally for the optional one-time bootstrap, not by the bot.
VM snapshots/backups can still contain secrets: keep them private.

## sshx

A full sshx URL is a bearer credential for a root shell inside the owner's container. Anyone holding it can join.
The bot verifies Discord ownership again in the backend; it tests DM delivery before launching a session.
Links only go to a DM edit; no channel or ephemeral-channel fallback contains a URL.
Terminal output is bounded, parsed for the official `https://sshx.io/s/…#…` shape and not logged.
URLs are not saved to SQLite or audit events. The temporary guest log is removed after reading.
Sessions use a bounded timeout (15 minutes default), never beyond the lease deadline, with a watchdog for expired containers.
Clicking sshx again kills the previous `sshx` process in that container. If delivery of a newly created link fails, the bot revokes it.
Timeout/revocation controls are convenience controls, not a security boundary against the guest's root user,
who can modify guest tools or launch independent relay sessions. The root user is already authorized inside that guest.
Protect Discord accounts and do not share links. Review sshx's external relay/security model and trust its dependencies separately.

## Host privilege and quotas

The `cloudy` Unix user belongs to Docker's group, which is **root-equivalent on the host**.
Treat the bot code, Python dependencies and Docker socket as a privileged management plane. Do not expose a Docker TCP API.
The sole sudo entry permits a root-owned, no-argument firewall check; it does not permit running an arbitrary command.
Host service hardening is defense in depth, not a boundary against someone who already controls the Docker client.

Tenants run as root only inside their containers, with all capabilities dropped except an explicit small allowlist,
no SYS_ADMIN/NET_ADMIN/NET_RAW, no privileged mode, no host mounts or published ports, no-new-privileges,
Docker's default seccomp policy, PID/no-file limits, capped logs, no extra swap, CPU/RAM cgroups and a real XFS writable-layer quota.
Root in a shared-kernel Docker container is still not equivalent to hardened microVM isolation.

The node must be dedicated. Admission includes reservations for stopped/expired-retained guests, host reserves,
actual free resources and the global slot limit. Untracked containers block new provisioning.
A real quota-probe container checks storage support. Quota options are never silently dropped.
Crash recovery checks labels and names; ambiguous Docker creation errors are inspected before releasing database reservations.
SQLite uses parameterized queries, WAL and unique owner constraints; a file lock prevents duplicate bot processes.
Never edit ownership/expiry labels or the database manually unless you understand the recovery model.

## Network

Dedicated tenant bridges use `cdy…` names. The host firewall blocks tenant-to-host input, private/metadata ranges,
cross-tenant traffic, unsolicited inbound traffic and IPv6. Default outbound policy only allows public TCP:80/443
plus DNS to Cloudflare/Quad9. No raw sockets are provided.
This reduces exposure; it does **not** prevent all abuse through allowed public HTTPS or substitute for bandwidth/rate limiting,
fraud/abuse review, kernel patching, monitoring and provider-allowed third-party hosting.
Administrator changes to firewall order/routing can invalidate assumptions. Re-audit after any network changes.
Deploy/start are blocked when the required firewall rules cannot be verified.
Do not change to permissive networking just to make an unsupported workload work without reviewing the risks.

## Lifetime, deletion and availability

Default lease: 30 days. Independent host timer checks expiration roughly every minute and stops expired running guests,
even if Discord is offline. The working bot deletes expired data after 72 hours; no implicit renewal.
Reinstall is destructive and requires a separate confirmation. It preserves the deadline, not the data.
Stop retains writable disk state; after a host restart, tenants stay stopped until an active owner explicitly starts them.
An offline machine cannot run its timer; stopped machines consume provider storage, and running machines consume resources.
The bot cannot report its own complete outage to Discord; use separate external monitoring.

## Before serving unknown people

Use per-tenant VM/microVM isolation or a separately validated sandbox runtime, a managed API boundary,
external monitoring, ingress/egress policies, billing caps, bandwidth controls, backups, incident response and an independent review.
`DOCKER_RUNTIME` is configurable, but no alternative runtime is installed or certified by this project.
No mining, resource-limit evasion, account cycling or fake idle-traffic helpers are included.
