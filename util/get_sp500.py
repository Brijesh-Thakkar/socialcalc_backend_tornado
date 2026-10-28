
import urllib.request, urllib.error, urllib.parse
from . import BeautifulSoup

wikiurl = "http://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


data = open("sp500.html").read()
soup = BeautifulSoup.BeautifulSoup(data)
table = soup.findAll('table',attrs={"class":"wikitable sortable"})
rows = table[0].findAll("tr")
rows = rows[1:]

for row in rows:
    cols = row.findAll('td')
    print(cols[0].a.string)

    
