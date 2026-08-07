import requests

url = "https://www.naukri.com/jobapi/v3/search"

params = {
    "noOfResults": 20,
    "urlType": "search_by_keyword",
    "searchType": "adv",
    "keyword": "ai engineer",
    "pageNo": 1,
    "jobAge": 3,
    "k": "ai engineer",
    "seoKey": "ai-engineer-jobs",
    "src": "directSearch",
}

headers = {
    "accept": "application/json",
    "content-type": "application/json",
    "appid": "109",
    "systemid": "Naukri",
    "clientid": "d3skt0p",
    "gid": "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE",
    "nkparam": "RqzkMtuSHm+88SLSmuHYynmsE7VjQpYtDfNc+6rXcBjVFx4kbamJcWcW7PDRGSPpjCYyZ+1OGhjpwUTTyCk8HQ==",
    "referer": "https://www.naukri.com/ai-engineer-jobs?k=ai%20engineer&jobAge=3",
    "user-agent": "Mozilla/5.0",
}

response = requests.get(url, params=params, headers=headers)
print(response.status_code)
print(response.json())