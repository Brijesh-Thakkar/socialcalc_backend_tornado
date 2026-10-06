#
# parse ann/cf and balance sheet into xbrl
#
#

from . import urlutil
from . import BeautifulSoup
from .BeautifulSoup import Tag,NavigableString
from . import xbrlmap
import json

#Qtr,Ann
#Income, Balance, CashFlow
msnbase = "http://moneycentral.msn.com/investor/invsub/results/statemnt.aspx?Symbol=%s&lstStatement=%s&stmtView=%s"

def cleanStr(line):
    """replace all nonalpha chars with spaces
    and then split on spaces, and then join with
    spaces
    """
    newstr = ""
    for i in line:
        if ord(i) in range(32,126):
            newstr = newstr+i
        else:
            newstr = newstr+' '
    newstr = newstr.strip()
    lis = newstr.split()
    newl = ' '.join(lis)    
    return newl

lineExceptions = ["&nbsp;","Assets","Liabilities and Shareholders' Equity"]

def getCols(row):
    cols = row.findAll('td')
    retcol = []
    for j in range(len(cols)):
        if cols[j].span != None:
            #print cols[j].span.string
            s = cleanStr(cols[j].span.renderContents())
        else:
            s = cols[j].string
        #print s
        retcol.append(s)
    if len(retcol) > 0 and retcol[0] not in lineExceptions:
        retcol[0] = xbrlmap.getXbrlTag(retcol[0])
    return retcol

def dupRow(rows,row):
    col0 = [cols[0] for cols in rows if len(cols) > 0]
    if row[0] in col0 and row[0] != "&nbsp;":
        #print "dup - %s"%row[0]
        return True
    else:
        #check if all cols are nbsp
        nonnbsp = [x for x in row if x != "&nbsp;"]
        if len(nonnbsp) <= 1:
            #print "dup %s"%row
            return True;
        return False
    
def getTable(table):
    rows = table.findAll('tr')
    retrows = []
    for i in range(len(rows)):
        #print "------%d--------"%i
        if rows[i].table != None:
            newr = getTable(rows[i].table)
            for i in newr:
                if not dupRow(retrows,i):
                    retrows.append(i)
        else:
            cols = getCols(rows[i])
            if len(cols) > 0 and not dupRow(retrows,cols):
                retrows.append(cols)
    return retrows

def printTable(rows):
    for i in rows:
        for j in i:
            print(j+",", end=' ')
        print("")

def getHtmlizeRow(row):
    r = "<tr>"
    for c in row:
        r = r +"<td>"+c+"</td>"
    r += "</tr>"
    return r

def getHtmlizeTable(rows):
    tab = "<table>"
    for row in rows:
        tab += getHtmlizeRow(row)
    tab += "</table>"
    return tab

def getSheetTable(rows, rownum):
    colstr = ""
    colnames = ["A","B","C","D","E","F"]
    rowind = 1
    for row in rows:
        colnum = 0
        for col in row:
            colname = colnames[colnum]
            colnum = colnum+1
            if (colnum == 1):
                coltype = "t"
            else:
                if rowind <= 2:
                    coltype = "t"
                else:
                    coltype = "v"
            col1 = col.replace(",","")
            colstr = colstr+"cell"+":"+colname+str(rownum)+":"+coltype+":"+col1+r"\n"
        rownum=rownum+1
        rowind=rowind+1
    return colstr,rownum


def getNormalizedTable(rows,type):
    """
    Normalize the IS,BS,CF to contain the same rows in the same order
    """
    map = {}
    # fillmap
    length = len(rows[0])
    for row in rows:
        map[row[0]] = row
    # get canonical map
    canonlist = xbrlmap.getNormalizedList(type)
    retrows = []
    for item in canonlist:
        if item in map:
            retrows.append(map[item])
        else:
            #append an empty row
            newrow = []
            newrow.append(item)
            for i in range(length-1):
                newrow.append("0")
            retrows.append(newrow)
    return retrows

def getCSVTable(rows):
    colstr = ""
    for row in rows:
        for col in row:
            col1 = col.replace(",","")
            colstr = colstr+col1+" , "
        colstr = colstr + "\n"
    return colstr

def getFinTable(soup):
    fintab = soup.findAll('table',attrs={'class':'ftable'})    
    return fintab[0]
    
def getFinStmt(soup):
    fintab = soup.findAll('table',attrs={'class':'ftable'})
    #print fintab
    # sanitize the columns
    #print fintab
    rows = getTable(fintab[0])
    #printTable(rows)
    return rows

def createXmlDoc(sym):
    soup = BeautifulSoup.BeautifulSoup()
    tag = Tag(soup,"xml")
    soup.insert(0, tag)
    tag['id']=sym
    return soup

def addTableToXml(soup, rows, stmttype):
    context = rows[0]
    count = len(soup.xml.contents)
    for row in rows[1:]:
        name = row[0]
        for col in range(1,len(row)):
            tag = Tag(soup,name.lower())
            tag["context"] = context[col]
            tag["type"] = stmttype
            text = NavigableString(row[col])
            item = soup.find(name,attrs={"context":context[col],"type":stmttype})
            if item != None:
                # if item exists, replace
                item.extract()
            tag.insert(0,text)
            soup.xml.insert(count,tag)
            count = count+1

def getAllTables(sym, cache=False):
    #add years c,i,b
    #add q     c,i,b
    soup1 = createXmlDoc(sym)
    
    url = msnbase % (sym,"Income", "Ann")
    print("fetch:",url)
    data = urlutil.getUrl(url,cache)
    open("tmp-inc.htm","w").write(data)
    soup = BeautifulSoup.BeautifulSoup(data)
    rows = getFinStmt(soup)
    addTableToXml(soup1, rows, "i")

    url = msnbase % (sym,"Balance", "Ann")
    print("fetch:",url)
    data = urlutil.getUrl(url,cache)
    open("tmp-bal.htm","w").write(data)    
    soup = BeautifulSoup.BeautifulSoup(data)
    rows = getFinStmt(soup)
    addTableToXml(soup1, rows, "b")

    url = msnbase % (sym,"CashFlow", "Ann")
    print("fetch:",url)
    data = urlutil.getUrl(url,cache) 
    open("tmp-cf.htm","w").write(data)       
    soup = BeautifulSoup.BeautifulSoup(data)
    rows = getFinStmt(soup)
    addTableToXml(soup1, rows, "c")

    return soup1

def get_ticker_data(sym, typ, period):
    url = msnbase % (sym,typ,period)
    #print "fetch:",url
    data = urlutil.getUrl(url)
    soup = BeautifulSoup.BeautifulSoup(data)
    rows = getFinStmt(soup)
    rows = getNormalizedTable(rows, typ)
    return rows
    #return getCSVTable(rows)    
    

def demo_get_data(sym, typ, period):    
    url = msnbase % (sym,typ,period)
    #print "fetch:",url
    data = urlutil.getUrl(url)
    soup = BeautifulSoup.BeautifulSoup(data)
    rows = getFinStmt(soup)
    rows = getNormalizedTable(rows, typ)
    return getCSVTable(rows)    

def demo_get_sheet_data(sym, typ, period, startrow):
    url = msnbase % (sym,typ,period)
    #print "fetch:",url
    data = urlutil.getUrl(url)
    soup = BeautifulSoup.BeautifulSoup(data)
    rows = getFinStmt(soup)
    rows = getNormalizedTable(rows, typ)
    #print rows
    return getSheetTable(rows, startrow)        

def demo1():
    str1 = demo_get_data("msft","Balance", "Ann")
    str2 = demo_get_data("msft","Income", "Ann")
    str3 = demo_get_data("msft","CashFlow", "Ann")
    print(str1+"\n\n"+str2+"\n\n"+str3)

def demo2(sym):
    startrow = 1
    str1,startrow = demo_get_sheet_data(sym,"Balance", "Ann", startrow)
    str2,startrow = demo_get_sheet_data(sym,"Income", "Ann", startrow+2)
    str3,startrow = demo_get_sheet_data(sym,"CashFlow", "Ann", startrow+2)
    print(str1+str2+str3)



savetemplate1 = """{"numsheets":3,"currentid":"sheet1","currentname":"balance","sheetArr":{"sheet1":{"sheetstr":{"savestr":"version:1.5"""
savetemplate2 = """},"name":"balance"},"sheet2":{"sheetstr":{"savestr":"version:1.5"""
savetemplate3 = """},"name":"income"},"sheet3":{"sheetstr":{"savestr":"version:1.5"""
savetemplate4 = """},"name":"cashflow"}}}"""

def workbook(sym):
    startrow = 2
    str1,startrow = demo_get_sheet_data(sym,"Balance", "Ann", 2)
    str2,startrow = demo_get_sheet_data(sym,"Income", "Ann", 2)
    str3,startrow = demo_get_sheet_data(sym,"CashFlow", "Ann", 2)
    print(savetemplate1+"\\n"+str1+'"'+savetemplate2+"\\n"+str2+'"'+savetemplate3+"\\n"+str3+'"'+savetemplate4)


def getJsonStmt(tick,typ,period):
    url = msnbase % (tick,typ,period)
    data = urlutil.getUrl(url)
    soup = BeautifulSoup.BeautifulSoup(data)
    rows = getFinStmt(soup)
    rows = getNormalizedTable(rows, typ)
    stmt = {}
    for i in rows:
        stmt[i[0]] = i[1:]
    return stmt


def demojson(sym):
    stmt = {}
    stmt["ticker"] = sym
    stmt["Income"] = getJsonStmt(sym, "Income", "Ann")
    stmt["Balance"] = getJsonStmt(sym, "Balance", "Ann")
    stmt["CashFlow"] = getJsonStmt(sym, "CashFlow", "Ann")    
    return stmt

def demo():
    url = msnbase % ("msft","Income", "Qtr")
    #url = msnbase % ("msft","Balance", "Qtr")
    #url = msnbase % ("msft","CashFlow", "Qtr")        
    print("fetch:",url)
    data = urlutil.getUrl(url)
    #open('msn-cf.htm','w').write(data)
    #data = open('msn-bal.htm').read()
    #data = open('msn-inc.htm').read()
    #data = open('msn-cf.htm').read()        
    soup = BeautifulSoup.BeautifulSoup(data)
    rows = getFinStmt(soup)
    #table = getFinTable(soup)
    #print table.prettify()
    print(getCSVTable(rows))
    #soup1 = createXmlDoc("msft")
    #addTableToXml(soup1, rows, "c")
    #print soup1.prettify()
    

if __name__ == "__main__":
    import sys
    typ = sys.argv[1]
    if typ == "json":
        tick1 = sys.argv[2]
        tick2 = sys.argv[3]
        tick3 = sys.argv[4]
        data = {}
        if (tick1 != "---"):
            data["tick1"] = demojson(tick1)
        else:
            data["tick1"] = {}
        if (tick2 != "---"):
            data["tick2"] = demojson(tick2)
        else:
            data["tick2"] = {}
        if (tick3 != "---"):
            data["tick3"] = demojson(tick3)
        else:
            data["tick3"] = {}
        print(json.dumps(data))
    else:
        #demo1()
        #demo2(sym)
        sym = sys.argv[2]
        workbook(sym)
