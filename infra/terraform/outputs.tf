output "server_ipv4" {
  description = "Public IPv4 address of the scanner VM"
  value       = hcloud_server.scanner.ipv4_address
}

output "server_ipv6" {
  description = "Public IPv6 address of the scanner VM"
  value       = hcloud_server.scanner.ipv6_address
}

output "ssh_command" {
  description = "SSH command to connect to the server"
  value       = "ssh -i ${var.ssh_public_key_path} deploy@${hcloud_server.scanner.ipv4_address}"
}

output "post_provision_steps" {
  description = "Manual steps after terraform apply"
  value       = <<-EOT
    1. SSH into the server and wait for cloud-init to finish:
       ssh root@${hcloud_server.scanner.ipv4_address}
       cloud-init status --wait

    2. Copy secrets to the server:
       scp .env root@${hcloud_server.scanner.ipv4_address}:/opt/arb-scanner/.env
       scp config.yaml root@${hcloud_server.scanner.ipv4_address}:/opt/arb-scanner/config.yaml
       scp kalshi_key.pem root@${hcloud_server.scanner.ipv4_address}:/opt/arb-scanner/kalshi_key.pem

    3. Set permissions and start services:
       ssh root@${hcloud_server.scanner.ipv4_address}
       chmod 600 /opt/arb-scanner/.env /opt/arb-scanner/kalshi_key.pem
       cd /opt/arb-scanner && docker compose -f docker-compose.prod.yml up -d

    4. Create deploy user for CI/CD:
       useradd -m -s /bin/bash deploy
       usermod -aG docker deploy
       mkdir -p /home/deploy/.ssh
       cp ~/.ssh/authorized_keys /home/deploy/.ssh/
       chown -R deploy:deploy /home/deploy/.ssh
       ln -s /opt/arb-scanner /home/deploy/arb-scanner

    5. Access dashboard via Tailscale:
       http://<tailscale-ip>:8000
  EOT
}
