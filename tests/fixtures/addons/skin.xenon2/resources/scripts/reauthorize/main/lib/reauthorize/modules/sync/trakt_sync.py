class Auth:
    def trakt_auth(self):
        your_expires = accountmgr.getSetting("trakt.expires")
        your_expires_float = float(your_expires)
        your_expires_rnd = int(your_expires_float)
        token = '{"access_token": "'
        refresh = f'","token_type": "bearer", "expires_in": 7776000, "refresh_token": "'
        expires = f'", "scope": "public", "created_at": '
        trakt_data = '%s%s%s%s%s%s}' %(token, your_token, refresh, your_refresh, expires, your_expires_rnd)
        addon.setSetting("authorization", trakt_data)

        expires_in = 7776000
