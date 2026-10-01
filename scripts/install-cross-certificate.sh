#!/bin/sh
# Optional chain compatibility must never install expired or untrusted material.
set -eu
[ "$#" -eq 2 ] || exit 64
certificate=$1
destination=$2
openssl x509 -in "$certificate" -noout >/dev/null
native_root=false
subject=$(openssl x509 -in "$certificate" -noout -subject -nameopt RFC2253)
for root in /usr/share/ca-certificates/mozilla/*.crt; do
    [ -f "$root" ] || continue
    root_subject=$(openssl x509 -in "$root" -noout -subject -nameopt RFC2253)
    [ "$root_subject" = "$subject" ] || continue
    root_issuer=$(openssl x509 -in "$root" -noout -issuer -nameopt RFC2253)
    if [ "${root_subject#subject=}" = "${root_issuer#issuer=}" ] \
        && [ "$(openssl x509 -in "$root" -noout -pubkey)" = "$(openssl x509 -in "$certificate" -noout -pubkey)" ] \
        && openssl x509 -in "$root" -noout -checkend 0 >/dev/null; then
        native_root=true
        break
    fi
done
if [ "$native_root" = true ]; then
    echo 'Skipping optional Root YE cross-certificate: the system already includes its native trust root' >&2
    rm -f "$destination"
elif ! openssl x509 -in "$certificate" -noout -checkend 0 >/dev/null; then
    echo 'Skipping expired optional Root YE cross-certificate; using the current system CA bundle' >&2
    rm -f "$destination"
else
    openssl verify -CAfile /etc/ssl/cert.pem "$certificate"
    mkdir -p "$(dirname "$destination")"
    install -m 644 "$certificate" "$destination"
fi
update-ca-certificates
