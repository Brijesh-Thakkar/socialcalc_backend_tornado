
#
#
#  Obtain all ticker symbols and save to a file
#  Used to validate a ticker
#
#  Obtain ticker list from nasdaq.com web site
#  periodically refresh it
#

def refreshTickers(fname):
    import mechanize
    br = mechanize.Browser()
    br.open(url)
    response = br.response()
    data = response.read()
    name = fname+"raw"
    f = open(name,"w")
    f.write(data)
    f.close()

tickers = {}
print("loading tickers...")
data = open("./util/nasdaq.txt").read()
datalist = data.split("\n")
#print len(datalist)
for i in datalist:
    if i != "":
        j = i.split(",")
        if j[0] != "":
            sym = j[0].replace('"',"")
            #print "adding "+sym
            tickers[sym] = sym



def isValidTicker(s):
    s1 = s.upper()
    #print s1
    if tickers.get(s1) != None:
        return True
    return False


if __name__ == "__main__":
    import sys
    #fname = sys.argv[1]
    #refreshTickers(fname)
    #data = processRawSymbols("symbolsraw")
    #f = open("symbols.txt","w")
    #f.write(data)
    #f.close()
    print(isValidTicker(sys.argv[1]))
    print(isValidTicker(sys.argv[2]))



