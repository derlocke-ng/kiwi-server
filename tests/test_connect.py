"""Which services the network's accounts can sign people in to, and where each one lives."""
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib"))
from kiwiserver import cli  # noqa: E402


class TestConnectable(unittest.TestCase):
    def test_each_service_has_its_name_and_its_callback(self):
        h = types.SimpleNamespace(hostname="papaya.home")
        names = {m: (where({}, h), path) for m, (label, where, path) in cli.CONNECTABLE.items()}
        self.assertEqual(names["git"], ("git.papaya.home", "/user/oauth2/accounts/callback"))
        self.assertEqual(names["cloud"], ("cloud.papaya.home", "/apps/user_oidc/code"))
        self.assertEqual(names["paperless"], ("paperless.papaya.home", "/accounts/oidc/accounts/login/callback/"))
        self.assertEqual(names["vault"], ("vault.papaya.home", "/identity/connect/oidc-signin"))
        self.assertEqual(names["immich"], ("immich.papaya.home", ("/auth/login", "/user-settings", "/api/oauth/mobile-redirect")))
        self.assertEqual(cli.PKCE, {"immich"})

    def test_a_vault_with_its_own_address_is_found_there(self):
        h = types.SimpleNamespace(hostname="papaya.home")
        where = cli.CONNECTABLE["vault"][1]
        self.assertEqual(where({"vault_domain": "https://pw.example.org/"}, h), "pw.example.org")
        self.assertEqual(where({"vault_domain": ""}, h), "vault.papaya.home")


if __name__ == "__main__":
    unittest.main()
