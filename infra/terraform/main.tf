# --- SSH Key ---

resource "hcloud_ssh_key" "deploy" {
  name       = "arb-scanner-deploy"
  public_key = file(var.ssh_public_key_path)
}

# --- Firewall ---

resource "hcloud_firewall" "scanner" {
  name = "arb-scanner"

  # SSH from allowed CIDRs
  rule {
    direction  = "in"
    protocol   = "tcp"
    port       = "22"
    source_ips = var.allowed_ssh_cidrs
  }

  # Tailscale WireGuard (UDP 41641) from anywhere — Tailscale handles its own auth
  rule {
    direction  = "in"
    protocol   = "udp"
    port       = "41641"
    source_ips = ["0.0.0.0/0", "::/0"]
  }
}

# --- Persistent Volume for PostgreSQL ---

resource "hcloud_volume" "pgdata" {
  name              = "arb-scanner-pgdata"
  size              = 20
  location          = var.location
  format            = "ext4"
  delete_protection = true
}

# --- Server ---

resource "hcloud_server" "scanner" {
  name        = "arb-scanner"
  server_type = var.server_type
  location    = var.location
  image       = "ubuntu-24.04"
  ssh_keys    = [hcloud_ssh_key.deploy.id]

  firewall_ids = [hcloud_firewall.scanner.id]

  user_data = templatefile("${path.module}/cloud-init.yml", {
    tailscale_auth_key = var.tailscale_auth_key
    ghcr_username      = var.ghcr_username
    ghcr_token         = var.ghcr_token
  })

  public_net {
    ipv4_enabled = true
    ipv6_enabled = true
  }
}

# --- Attach Volume ---

resource "hcloud_volume_attachment" "pgdata" {
  volume_id = hcloud_volume.pgdata.id
  server_id = hcloud_server.scanner.id
  automount = true
}
