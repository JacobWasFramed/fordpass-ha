import json
import logging
import os
import time

import requests

_LOGGER = logging.getLogger(__name__)
defaultHeaders = {
    "Accept": "*/*",
    "Accept-Language": "en-us",
    "User-Agent": "fordpass-ap/93 CFNetwork/1197 Darwin/20.0.0",
    "Accept-Encoding": "gzip, deflate, br",
}

apiHeaders = {
    **defaultHeaders,
    "Content-Type": "application/json",
}

region_lookup = {
    "UK&Europe": "1E8C7794-FF5F-49BC-9596-A1E0C86C5B19",
    "Australia": "5C80A6BB-CF0D-4A30-BDBF-FC804B5C1A98",
    "North America & Canada": "71A3AD0A-CF46-4CCF-B473-FC7FE5BC4592",
}

baseUrl = "https://usapi.cv.ford.com/api"

guardUrl = "https://api.mps.ford.com/api"


class Vehicle(object):
    # Represents a Ford vehicle, with methods for status and issuing commands

    def __init__(
        self, username, password, vin, region, saveToken=False, configLocation=""
    ):
        self.username = username
        self.password = password
        self.saveToken = saveToken
        self.region = region_lookup[region]
        self.vin = vin
        self.token = None
        self.expires = None
        self.expiresAt = None
        self.refresh_token = None
        if configLocation == "":
            self.token_location = "custom_components/fordpass/fordpass_token.txt"
        else:
            _LOGGER.debug(configLocation)
            self.token_location = configLocation

    def auth(self):
        """Authenticate and store the token"""

        data = {
            "client_id": "9fb503e0-715b-47e8-adfd-ad4b7770f73b",
            "grant_type": "password",
            "username": self.username,
            "password": self.password,
        }

        headers = {
            **defaultHeaders,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        # Fetch OAUTH token stage 1
        r = requests.post(
            "https://sso.ci.ford.com/oidc/endpoint/default/token",
            data=data,
            headers=headers,
        )

        if r.status_code == 200:
            _LOGGER.debug("Succesfully fetched token Stage1")
            result = r.json()
            data = {"code": result["access_token"]}
            headers = {**apiHeaders, "Application-Id": self.region}
            # Fetch OAUTH token stage 2 and refresh token
            r = requests.put(
                "https://api.mps.ford.com/api/oauth2/v1/token",
                data=json.dumps(data),
                headers=headers,
            )
            if r.status_code == 200:
                result = r.json()
                self.token = result["access_token"]
                self.refresh_token = result["refresh_token"]
                self.expiresAt = time.time() + result["expires_in"]
                if self.saveToken:
                    result["expiry_date"] = time.time() + result["expires_in"]
                    self.writeToken(result)
                return True
        else:
            r.raise_for_status()

    def refreshToken(self, token):
        # Token is invalid so let's try refreshing it
        data = {"refresh_token": token["refresh_token"]}
        headers = {**apiHeaders, "Application-Id": self.region}

        r = requests.put(
            "https://api.mps.ford.com/api/oauth2/v1/refresh",
            data=json.dumps(data),
            headers=headers,
        )
        if r.status_code == 200:
            result = r.json()
            if self.saveToken:
                result["expiry_date"] = time.time() + result["expires_in"]
                self.writeToken(result)
            self.token = result["access_token"]
            self.refresh_token = result["refresh_token"]
            self.expiresAt = time.time() + result["expires_in"]
        if r.status_code == 401:
            _LOGGER.debug("401 response stage 2: refresh stage 1 token")
            self.auth()

    def __acquireToken(self):
        # Fetch and refresh token as needed
        # If file exists read in token file and check it's valid
        if self.saveToken:
            if os.path.isfile(self.token_location):
                data = self.readToken()
            else:
                data = dict()
                data["access_token"] = self.token
                data["refresh_token"] = self.refresh_token
                data["expiry_date"] = self.expiresAt
        else:
            data = dict()
            data["access_token"] = self.token
            data["refresh_token"] = self.refresh_token
            data["expiry_date"] = self.expiresAt
        self.token = data["access_token"]
        self.expiresAt = data["expiry_date"]
        if self.expiresAt:
            if time.time() >= self.expiresAt:
                _LOGGER.debug("No token, or has expired, requesting new token")
                self.refreshToken(data)
                # self.auth()
        if self.token == None:
            # No existing token exists so refreshing library
            self.auth()
        else:
            _LOGGER.debug("Token is valid, continuing")
            pass

    def writeToken(self, token):
        # Save token to file to be reused
        with open(self.token_location, "w") as outfile:
            token["expiry_date"] = time.time() + token["expires_in"]
            _LOGGER.debug(token)
            json.dump(token, outfile)

    def readToken(self):
        # Get saved token from file
        try:
            with open(self.token_location) as token_file:
                token = json.load(token_file)
                return token
        except ValueError:
            _LOGGER.debug("Fixing malformed token")
            self.auth()
            with open(self.token_location) as token_file:
                token = json.load(token_file)
                return token

    def clearToken(self):
        if os.path.isfile("/tmp/fordpass_token.txt"):
            os.remove("/tmp/fordpass_token.txt")
        if os.path.isfile("/tmp/token.txt"):
            os.remove("/tmp/token.txt")
        if os.path.isfile(self.token_location):
            os.remove(self.token_location)

    def status(self):
        # Get the status of the vehicle

        self.__acquireToken()

        params = {"lrdt": "01-01-1970 00:00:00"}

        headers = {
            **apiHeaders,
            "auth-token": self.token,
            "Application-Id": self.region,
        }

        r = requests.get(
            f"{baseUrl}/vehicles/v4/{self.vin}/status", params=params, headers=headers
        )
        if r.status_code == 200:
            result = r.json()
            if result["status"] == 402:
                r.raise_for_status()
            return result["vehiclestatus"]
        if r.status_code == 401:
            _LOGGER.debug("401 with status request: start token refresh")
            data = dict()
            data["access_token"] = self.token
            data["refresh_token"] = self.refresh_token
            data["expiry_date"] = self.expiresAt
            self.refreshToken(data)
            self.__acquireToken()
            headers = {
                **apiHeaders,
                "auth-token": self.token,
                "Application-Id": self.region,
            }
            r = requests.get(
                f"{baseUrl}/vehicles/v4/{self.vin}/status",
                params=params,
                headers=headers,
            )
            if r.status_code == 200:
                result = r.json()
            return result["vehiclestatus"]
        else:
            r.raise_for_status()

    def guardStatus(self):
        # WIP current being tested
        self.__acquireToken()

        params = {"lrdt": "01-01-1970 00:00:00"}

        headers = {
            **apiHeaders,
            "auth-token": self.token,
            "Application-Id": self.region,
        }

        r = requests.get(
            f"{guardUrl}/guardmode/v1/{self.vin}/session",
            params=params,
            headers=headers,
        )
        return r.json()

    def start(self):
        """
        Issue a start command to the engine
        """
        return self.__requestAndPoll(
            "PUT", f"{baseUrl}/vehicles/v2/{self.vin}/engine/start"
        )

    def stop(self):
        """
        Issue a stop command to the engine
        """
        return self.__requestAndPoll(
            "DELETE", f"{baseUrl}/vehicles/v2/{self.vin}/engine/start"
        )

    def lock(self):
        """
        Issue a lock command to the doors
        """
        return self.__requestAndPoll(
            "PUT", f"{baseUrl}/vehicles/v2/{self.vin}/doors/lock"
        )

    def unlock(self):
        """
        Issue an unlock command to the doors
        """
        return self.__requestAndPoll(
            "DELETE", f"{baseUrl}/vehicles/v2/{self.vin}/doors/lock"
        )

    def enableGuard(self):
        """
        Enable Guard mode on supported models
        """
        self.__acquireToken()

        r = self.__makeRequest(
            "PUT", f"{guardUrl}/guardmode/v1/{self.vin}/session", None, None
        )
        _LOGGER.debug(r.text)
        return r

    def disableGuard(self):
        """
        Disable Guard mode on supported models
        """
        self.__acquireToken()
        r = self.__makeRequest(
            "DELETE", f"{guardUrl}/guardmode/v1/{self.vin}/session", None, None
        )
        _LOGGER.debug(r.text)
        return r

    def requestUpdate(self, vin=""):
        # Send request to refresh data from the cars module
        self.__acquireToken()
        if vin:
            vinnum = vin
        else:
            vinnum = self.vin
        status = self.__makeRequest(
            "PUT", f"{baseUrl}/vehicles/v2/{vinnum}/status", None, None
        )
        return status.json()["status"]

    def __makeRequest(self, method, url, data, params):
        """
        Make a request to the given URL, passing data/params as needed
        """

        headers = {
            **apiHeaders,
            "auth-token": self.token,
            "Application-Id": self.region,
        }

        return getattr(requests, method.lower())(
            url, headers=headers, data=data, params=params
        )

    def __pollStatus(self, url, id):
        """
        Poll the given URL with the given command ID until the command is completed
        """
        status = self.__makeRequest("GET", f"{url}/{id}", None, None)
        result = status.json()
        if result["status"] == 552:
            _LOGGER.debug("Command is pending")
            time.sleep(5)
            return self.__pollStatus(url, id)  # retry after 5s
        elif result["status"] == 200:
            _LOGGER.debug("Command completed succesfully")
            return True
        else:
            _LOGGER.debug("Command failed")
            return False

    def __requestAndPoll(self, method, url):
        self.__acquireToken()
        command = self.__makeRequest(method, url, None, None)

        if command.status_code == 200:
            result = command.json()
            return self.__pollStatus(url, result["commandId"])
        else:
            command.raise_for_status()
