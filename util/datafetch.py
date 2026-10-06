urlqtr = "http://www.advfn.com/p.php?pid=financials&btn=istart_date&mode=quarterly_reports&symbol=%s&istart_date=0"

urlann = "http://www.advfn.com/p.php?pid=financials&btn=start_date&mode=annual_reports&symbol=%s&start_date=%d"

urlannnostart = "http://www.advfn.com/p.php?pid=financials&btn=start_date&mode=annual_reports&symbol=%s"

import mechanize
from .BeautifulSoup import BeautifulSoup
import time
import zlib
import os.path

fnamePrefix = "./annualdata/"

def saveTickerData(ticker, start, data):
    #gzip ?
    fname = fnamePrefix+ticker+"-"+str(start)+".z"
    f = open(fname,"wb")
    f.write(zlib.compress(data))
    f.close()

def fileExists(ticker):
    fname = fnamePrefix+ticker+"-0"+".z"    
    return os.path.exists(fname)

def readTickerData(ticker,start):
    fname = fnamePrefix+ticker+"-"+str(start)+".z"
    f = open(fname,"rb")
    data = f.read()
    f.close()
    return zlib.decompress(data)

def getAndSaveTicker(url, ticker, start):
    br = mechanize.Browser()
    start = 0
    print("fetch "+ticker)
    br.open(url)
    response = br.response()
    data = response.read()
    saveTickerData(ticker, start, data)

def getTickerData(sym):
    msnbase = "http://moneycentral.msn.com/investor/invsub/results/statemnt.aspx?Symbol=%s&lstStatement=%s&stmtView=%s"
    url = msnbase % (sym,"Balance", "Ann")
    getAndSaveTicker(url,sym,1);
    
    url = msnbase % (sym,"Income", "Ann")
    getAndSaveTicker(url,sym,2);
        
    url = msnbase % (sym,"CashFlow", "Ann")
    getAndSaveTicker(url,sym,3);
    


    

tickers = {}
def loadTickers():
    print("loading tickers...")
    data = open("./nasdaq.txt").read()
    datalist = data.split("\n")
    print(len(datalist))
    for i in datalist:
        if i != "":
            j = i.split(",")
            if j[0] != "":
                sym = j[0].replace('"',"")
                #print "adding "+sym
                tickers[sym] = sym

badlist = {"SPE":"","GRR":""}                

def getTickers():
    count = 0
    for ticker in list(tickers.keys()):
        count = count + 1
        #if count < 18:
        #    continue
        if ticker.find("^") != -1:
            print(ticker+" bad ticker")
            continue
        if badlist.get(ticker) != None:
            continue
        # check if the ticker has been loaded
        if fileExists(ticker):
            print(ticker+" exists..")
        else:
            try:
                pass
                #getTickerData(ticker)
            except:
                print("Exception..")
                badlist[ticker] = ticker
                time.sleep(8)
        print(str(count)+"/"+str(len(tickers)))

if __name__ == "__main__":
    import sys
    loadTickers()
    getTickers()
