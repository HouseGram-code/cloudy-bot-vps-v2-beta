// This is never called by the bot. Run explicitly on your own computer.
// Freestyle resource allocation can incur real charges.
import { Freestyle } from 'freestyle';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const project = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const receipt = path.join(project, '.freestyle-host.json');
const quote = (s) => `'${s.replaceAll("'", "'\\''")}'`;
if (process.env.FREESTYLE_ALLOW_BILLABLE_CREATE !== 'YES') {
  throw new Error('Read docs/FREESTYLE_RU.md, then set FREESTYLE_ALLOW_BILLABLE_CREATE=YES to consent to creating and sizing a fresh VM.');
}
if (!process.env.FREESTYLE_API_KEY) throw new Error('Set a NEW FREESTYLE_API_KEY in your environment, not in source.');
try {
  await fs.access(receipt);
  throw new Error('A host receipt already exists. Inspect your existing VM; do not create duplicates.');
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}
const env = await fs.readFile(path.join(project, '.env'), 'utf8');
if (/DISCORD_TOKEN\s*=\s*(?:REPLACE|YOUR_|PASTE_)/i.test(env) || !/^DISCORD_TOKEN\s*=/m.test(env)) {
  throw new Error('Configure a newly regenerated Discord token in the local .env first.');
}
if (/^FREESTYLE_API_KEY\s*=/m.test(env)) throw new Error('Keep the Freestyle API key OUT of the bot .env. Use your local environment only.');
const slug = `cloudy-host-${Date.now().toString(36)}`;
const freestyle = new Freestyle();
let vm, vmId, resized = false;
try {
  const created = await freestyle.vms.create({
    slug,
    idleTimeoutSeconds: null, // Official always-on setting, not fake-traffic keepalive.
    firewall: {rules: [{action: 'allow', source: {}, destination: {public: true}}]},
    metadata: {project: 'cloudy-vps', purpose: 'authorized-discord-beta'},
  });
  vm = created.vm;
  vmId = created.vmId;
  await fs.writeFile(receipt, JSON.stringify({vmId, slug, installed: false}, null, 2), {mode: 0o600, flag: 'wx'});
  // Official SDK units: memory/storage in MiB. No Free-tier bypass or overbooking.
  await vm.resize({cpu: 4, memory: 20 * 1024, storage: 128 * 1024});
  resized = true;
  const base = '/opt/cloudy-vps';
  async function command(text) {
    const result = await vm.exec({command: text, timeoutMs: 300000});
    if (result.statusCode !== 0) throw new Error('Remote setup command failed; inspect the VM install log.');
    return result;
  }
  await command(`mkdir -p ${base} && chmod 755 ${base}`);
  const allowed = ['cloudy', 'scripts', 'containers', 'deploy', 'docs', 'requirements.txt', 'requirements-dev.txt', 'README.md', 'START_HERE_RU.md', '.env.example'];
  async function upload(relative) {
    const local = path.join(project, relative);
    const stat = await fs.lstat(local);
    if (stat.isSymbolicLink()) throw new Error('Symlinks are not accepted in deployment source.');
    if (stat.isDirectory()) {
      if (['node_modules', '__pycache__', '.venv', '.git'].includes(path.basename(relative))) return;
      await command(`mkdir -p ${quote(base + '/' + relative)}`);
      for (const name of await fs.readdir(local)) await upload(path.join(relative, name));
    } else if (stat.isFile()) {
      if (relative.endsWith('.pyc') || path.basename(relative).startsWith('.env') && relative !== '.env.example') return;
      const text = await fs.readFile(local, 'utf8');
      await vm.fs.writeTextFile(base + '/' + relative, text);
    }
  }
  for (const relative of allowed) await upload(relative);
  // The bot token goes only to this private, newly created VM, never a Git repository.
  await vm.fs.writeTextFile(base + '/.env', env);
  await command(`chmod 600 ${base}/.env`);
  await command(`nohup /bin/bash ${base}/scripts/freestyle-install.sh > /var/log/cloudy-install.log 2>&1 < /dev/null &`);
  await fs.writeFile(receipt, JSON.stringify({vmId, slug, installationStarted: true}, null, 2), {mode: 0o600});
  console.log('Fresh VM created and installation started:', slug);
  console.log('Use the Freestyle VM terminal: tail -n 80 /var/log/cloudy-install.log');
  console.log('Then: systemctl status cloudy-vps --no-pager');
  console.log('Installation success has NOT yet been verified. Charges follow your plan.');
} catch (error) {
  if (vm && !resized) {
    // Only the newly created, empty VM is deleted if sizing was rejected.
    try {
      await vm.delete();
      await fs.rm(receipt, {force: true});
      console.error('Sizing failed; the new empty VM was deleted. Check your account limits.');
    } catch {
      console.error('Setup failed. Inspect and delete the newly created VM in your dashboard to stop possible charges.');
    }
  } else {
    console.error('Setup was not completed. Inspect the VM and install log before retrying; do not create duplicate hosts.');
  }
  // SDK errors can contain request details: do not print tokens or full payloads.
  console.error('Failure type:', error?.name ?? 'Error');
  process.exitCode = 1;
}
