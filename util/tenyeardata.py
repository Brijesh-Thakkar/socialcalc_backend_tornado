#http://www.advfn.com/p.php?pid=financials&btn=quarterly_reports&mode=&symbol=%s

urlqtr = "http://www.advfn.com/p.php?pid=financials&btn=istart_date&mode=quarterly_reports&symbol=%s&istart_date=0"

urlann = "http://www.advfn.com/p.php?pid=financials&btn=start_date&mode=annual_reports&symbol=%s&start_date=%d"

urlannnostart = "http://www.advfn.com/p.php?pid=financials&btn=start_date&mode=annual_reports&symbol=%s"

import mechanize
from .BeautifulSoup import BeautifulSoup
import time
import zlib
import os.path
from . import analyzetenyear

def saveTickerData(ticker, start, data):
    #gzip ?
    fname = "./annualdata/"+ticker+"-"+str(start)+".z"
    f = open(fname,"wb")
    f.write(zlib.compress(data))
    f.close()

def fileExists(ticker):
    fname = "./annualdata/"+ticker+"-0"+".z"    
    return os.path.exists(fname)

def readTickerData(ticker,start):
    fname = "./annualdata/"+ticker+"-"+str(start)+".z"
    f = open(fname,"rb")
    data = f.read()
    f.close()
    return zlib.decompress(data)

def getTickerDataTenYear(ticker):
    br = mechanize.Browser()
    start = 0
    #print "fetch "+ticker+" "+str(start)
    br.open(urlannnostart%(ticker))
    response = br.response()
    data = response.read()
    soup = BeautifulSoup(data)
    selected = soup.findAll('select',attrs={"id":"start_dateid"})
    #print len(selected)
    #print selected
    years = 0
    if len(selected) == 0:
        #print "no options data found"
        pass
    else:
        options = selected[0].findAll('option')
        #print options
        years = len(options)
        #print "years = "+str(years)
    #analyzetenyear.printYears(data)
    analyzetenyear.stuffData(data)
    if years == 0:
        #print "returning"
        return
    prevstart = years - 10
    if prevstart < 0:
        prevstart = 0
    #print "sleeping.."
    time.sleep(2)
    #print "fetch "+ticker+" "+str(prevstart)
    br.open(urlann%(ticker,prevstart))
    response = br.response()
    data = response.read()
    #analyzetenyear.printYears(data)
    analyzetenyear.stuffData(data)
    analyzetenyear.printFormattedData()
    #analyzetenyear.getStuffedData()
    
    

def getTickerData(ticker):
    br = mechanize.Browser()
    start = 0
    print("fetch "+ticker+" "+str(start))
    br.open(urlann%(ticker,start))
    response = br.response()
    data = response.read()
    saveTickerData(ticker, start, data)
    start = start + 5
    #delay some time
    print("sleeping..")
    time.sleep(5)
    soup = BeautifulSoup(data)
    selected = soup.findAll('select',attrs={"id":"start_dateid"})
    #print len(selected)
    #print selected
    options = selected[0].findAll('option')
    print(options)
    years = len(options)
    print("years = "+str(years))
    while start < years:
        print("fetch "+ticker+" "+str(start))
        br.open(urlann%(ticker,start))
        response = br.response()
        data = response.read()
        saveTickerData(ticker, start, data)
        print("sleeping..")
        time.sleep(5)
        start = start + 5

tickers = {}
def loadTickers():
    #print "loading tickers..."
    data = open("./nasdaq.txt").read()
    datalist = data.split("\n")
    #print len(datalist)
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
        if count < 18:
            continue
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
                getTickerData(ticker)
                analyzetenyear.analyzeTicker(ticker)
            except:
                print("Exception..")
                badlist[ticker] = ticker
                time.sleep(8)
        print(str(count)+"/"+str(len(tickers)))

if __name__ == "__main__":
    #import sys
    #ticker = sys.argv[1]
    #getTickerData(ticker)
    #print readTickerData(ticker,0)
    #print fileExists(ticker)
    #getTickers()
    import sys
    getTickerDataTenYear(sys.argv[1]);
