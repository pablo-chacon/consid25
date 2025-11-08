import requests

class ConsiditionClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key
        self.headers = {"x-api-key": self.api_key}
    
    def post_game(self, data: object):
        return self.request("POST", "/game", json=data)

    def get_map(self, map_name: str):
        return self.request("GET", "/map", params={"mapName": map_name})

    def request(self, method: str, endpoint: str, **kwargs):
        url = f"{self.base_url}{endpoint}"
        response = requests.request(method, url, headers=self.headers, verify=False, **kwargs)
        response.raise_for_status()
        return response.json()
