
import mechanize
from .BeautifulSoup import BeautifulSoup
import time
import zlib
import os.path
import MySQLdb
import datetime

def readTickerData(ticker,start):
    fname = "./annualdata/"+ticker+"-"+str(start)+".z"
    print("reading "+fname)
    f = open(fname,"rb")
    data = f.read()
    f.close()
    return zlib.decompress(data)

def analyzeData(data):
    soup = BeautifulSoup(data)
    tables = soup.findAll('table',attrs={"width":"705"})
    rows = tables[0].findAll('tr')
    print(len(rows))
    count = 0
    for row in rows:
        cols = row.findAll('td')
        #print len(cols)
        for col in cols:
            if col.string != None:
                print(col.string+",", end=' ')
                #break
        count = count+1
        print("")


def getRows(data):
    soup = BeautifulSoup(data)
    tables = soup.findAll('table',attrs={"width":"705"})
    rows = tables[0].findAll('tr')
    return rows
    

def getCol(rows,n):
    # find the size of rows
    yearrow = rows[1]
    yearcols = yearrow.findAll('td')
    if (len(yearcols) <= n):
        return None
    retcol = {}
    for row in rows:
        cols = row.findAll('td')
        if len(cols) > n and len(cols) >= 2:
            col = cols[n]
            if (col.string != None) and (cols[0].string):
                retcol[cols[0].string] = cols[n].string
    return retcol

def fileExists(ticker, start):
    fname = "./annualdata/"+ticker+"-"+str(start)+".z"    
    return os.path.exists(fname)

def printYears(data):
    rows = getRows(data)
    for i in range(1,6):
        cols = getCol(rows,i)
        if cols != None and len(cols) > 10:
            #print cols
            print(len(cols),cols['year end date'])

ticker10yearData = {}

def stuffData(data):
    global ticker10yearData
    rows = getRows(data)
    for i in range(1,6):
        cols = getCol(rows,i)
        if cols != None and len(cols) > 10:
            #print cols
            #print len(cols),cols['year end date']
            year = cols['year end date']
            year = (year.split("/"))[0]
            if ticker10yearData.get(year, None) == None:
                #print "setting "+year
                ticker10yearData[year] = cols

def getYearOrder():
    year = datetime.date.today().year
    return list(range(year,year-12,-1))

def getPrintOrder():
    data = open("./util/order.txt").read()
    orderlis = data.split("\n")
    ret = []
    for i in orderlis:
        itemlis = i.split(",")
        if len(itemlis) > 1 and itemlis[1] == "Y":
            if len(itemlis) > 2 and itemlis[2] == 't':
                ret.append([itemlis[0],"t"])
            else:
                ret.append([itemlis[0],"v"])                
    #print ret
    return ret
    
savetemplate1 = """{"numsheets":1,"currentid":"sheet1","currentname":"statements","sheetArr":{"sheet1":{"sheetstr":{"savestr":"version:1.5"""
savetemplate2 = """},"name":"statements"}}}"""


def printFormattedData():
    #read the order into a list
    #get year order into a list
    yearlis = getYearOrder()
    orderlis = getPrintOrder()
    colnames = ["A","B","C","D","E","F","G","H","I","J","K"]
    colstr = ""
    # assume all values text for now
    rownum = 1            
    for item in orderlis:
        colnum = 0
        colname = colnames[colnum]
        colnum = colnum+1
        coltype = "t"
        col1 = item[0]
        colstr = colstr+"cell"+":"+colname+str(rownum)+":"+coltype+":"+col1+r"\n"
        coltype = item[1]        
        for year in yearlis:
            yearstr = str(year)
            if ticker10yearData.get(yearstr,None) == None:
                continue
            try:
                col1 = ticker10yearData[yearstr][item[0]]
                #print year,col1                
                colname = colnames[colnum]
                colnum = colnum+1
                if coltype == 'v':
                    col1 = col1.replace(",","")
                colstr = colstr+"cell"+":"+colname+str(rownum)+":"+coltype+":"+col1+r"\n"
            except:
                #print year,item[0],"not found"                
                pass
        rownum = rownum+1
    colstr = savetemplate1+"\\n"+colstr+'"'+savetemplate2
    print(colstr)
    return colstr
            
    
def getStuffedData():
    for keys in list(ticker10yearData.keys()):
        print(keys)

def analyzeTicker(ticker):
    start = 0
    print(ticker,"-----")
    while fileExists(ticker, start):
        data = readTickerData(ticker, start)
        analyzeData(data)
        #print data
        rows = getRows(data)
        #print rows
        for i in range(1,6):
            cols = getCol(rows,i)
            if cols != None and len(cols) > 10:
                #print cols
                print(len(cols),cols['year end date'])
        start = start + 5
        


def analyzeAllTickers():
    for ticker in list(tickers.keys()):
        try:
            analyzeTicker(ticker)
        except:
            print("Exception")

tickers = {}


def loadTickers():            
    print("loading tickers...")
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

dbconn = None

def connectDb():
    global dbconn
    if dbconn != None:
        return dbconn
    conn = MySQLdb.connect(host="127.0.0.1",user="ai",passwd="ai",db="aspiringinvestments")
    dbconn = conn
    return conn

def disconnectDb():
    global dbconn
    if dbconn != None:
        dbconn.close()
        dbconn = None
    
def createDbTable():
    conn = connectDb()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE TickerAnnualData (
           id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
           ticker VARCHAR(20) NOT NULL,
           year VARCHAR(20) NOT NULL,
           data LONGBLOB
        )    
        """)

if __name__ == "__main__":
    #loadTickers()
    import sys
    analyzeTicker(sys.argv[1])
    #connectDb()
    
    #disconnectDb()
