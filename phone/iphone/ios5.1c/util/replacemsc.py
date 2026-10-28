
import sys

if (len(sys.argv) < 4):
    print("Usage: replacemsc.py indexfile mscfile outfile")
    exit()

indexfile = sys.argv[1]
mscfile = sys.argv[2]
outputfile = sys.argv[3]

print("replace %s in file %s, and out %s"%(mscfile,indexfile,outputfile))

print(indexfile)

filedata = open(indexfile).read()

#print filedata

index1 = filedata.find("<textarea");
if (index1 == -1):
    print("Error 1")
    exit()
index2 = filedata.find(">",index1);
if (index2 == -1):
    print("Error 2")
    exit()

index3 = filedata.find("</textarea>");
if (index3 == -1):
    print("Error 3")
    exit()


startstr = filedata[:index2+1]
tailstr = filedata[index3:]

mscdata = open(mscfile).read()

mixeddata = startstr+mscdata+tailstr

out = open(outputfile,"w")
out.write(mixeddata)
out.close()

