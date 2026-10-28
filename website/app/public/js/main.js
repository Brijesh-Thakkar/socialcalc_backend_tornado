// Javascript functions
var selectedFile = "default";
var htmlEncryptionKey = "EncryptionKeyisLooooooooooooooooooongEnough";
var metaInfoFileName = "MetaFileInfoLoooooooooooooooooooooooooooongEnough";
var initialSelectedSheetButton = 1;
var spreadsheet = null;
var workbook = null;
var workbookcontrol = null;
var SocialCalc;
if (!SocialCalc) SocialCalc = {};
var Global = {};



function setLastEditedFileName(filename){
    // check meta file exists
    var filedata = window.localStorage.getItem(metaInfoFileName);
    var fileobj = {};
    if (filedata) {
        try {
            fileobj = JSON.parse(filedata);
        } catch (e) {
            fileobj = {};
        }
    } else {
        fileobj = {};
    }
    fileobj["lastedited"] = filename;
    window.localStorage.setItem(metaInfoFileName, JSON.stringify(fileobj));
}

function getLastEditedFileName(){
    var filedata = window.localStorage.getItem(metaInfoFileName);
    if (filedata) {
        try {
            var fileobj = JSON.parse(filedata);
            return fileobj["lastedited"]
        } catch (e) {
            return null;
        }
    }
    return null;
}

function getLastEditedFileData(){

    console.log("in init file data")
    var lasteditedfile = getLastEditedFileName();
    console.log("in init file data name:"+lasteditedfile)
    if (lasteditedfile) {
        if (lasteditedfile != "default") {
            var filedata = window.localStorage.getItem(lasteditedfile);
            if (filedata != null)
                return decodeURIComponent(filedata);
        } else {
            return document.getElementById("sheetdata").value
        }
    }
    console.log("no init file data")
    return null;
}



function loadAndStartUpApp(msc){

    spreadsheet = new SocialCalc.SpreadsheetControl();
    workbook = new SocialCalc.WorkBook(spreadsheet);
    workbook.InitializeWorkBook("sheet1");
    
      
    spreadsheet.InitializeSpreadsheetControl("tableeditor", 0, 0, 0);
    spreadsheet.ExecuteCommand('redisplay', '');
    
    
    workbookcontrol = new SocialCalc.WorkBookControl(workbook,"workbookControl","sheet1");
    workbookcontrol.InitializeWorkBookControl();
    
    //selectedFile = "default";
    initialSelectedSheetButton = 1;
    initialFileLoadData = null;
    SocialCalc.WorkBookControlLoad(msc);


    spreadsheet.DoOnResize();

    var ele = document.getElementById('te_griddiv');
    ele.style.height= "1000px";

    //Temporary fix for input
    activateFooterButton(2);
    activateFooterButton(1);


}

SocialCalc.oldBtnActive = 1;

function getSheetIds() {
    var control = SocialCalc.GetCurrentWorkBookControl();
    var sheets = [];
    for (key in control.sheetButtonArr) {
        //console.log(key);
        sheets.push(key);
    }
    return sheets;
}

function activateFooterButton(index) {
   if (index == SocialCalc.oldBtnActive) return;

   var oldbtn = "footerbtn"+ SocialCalc.oldBtnActive;
   var newbtn = "footerbtn"+ index;
   var sheets = getSheetIds()
   // disable active edit boxes
   var control = SocialCalc.GetCurrentWorkBookControl();
   var spreadsheet = control.workbook.spreadsheet;      
   var ele = document.getElementById(spreadsheet.formulabarDiv.id);
   if (ele) {
       SocialCalc.ToggleInputLineButtons(false);       
      var input = ele.firstChild;
      input.style.display="none";
      spreadsheet.editor.state = "start";
   }
   SocialCalc.WorkBookControlActivateSheet(sheets[index-1]);

   SocialCalc.oldBtnActive = index;
}


SocialCalc.ToggleInputLineButtons = function(show) {
    var bele = document.getElementById("testtest");
    if (!bele) return;
    if (show) {
        bele.style.display = "inline";
    } else {
        bele.style.display = "none";
    }
}
SocialCalc.InputLineClearText = function() {
    spreadsheet.editor.inputBox.SetText("");
}


/*SocialCalc.Callbacks.broadcast = function(type, data) {
    
}*/


function getFormattedTimestamp(timestr) {
    var d = new Date(timestr);
    if (d) {
        return d.toLocaleString()
    }
    return "";
}

function getFileDateString(filename) {
    var filedata = window.localStorage.getItem(filename);
    if (filedata) {
        filedata = decodeURIComponent(filedata);
        var data = JSON.parse(filedata);
        if (data && data["timestamp"]) {
            return getFormattedTimestamp(data["timestamp"])
        }
    }
    return ""
}

function updateFileName(fname) {

    if (selectedFile != fname) {
      setLastEditedFileName(fname);
    }

    selectedFile = fname;
    // Aspiring.AutoSave.selectedFile = fname;

    //document.getElementById("indexPage-fname").innerHTML="Editing: "+fname;


}


function showPrintDialog(){
    var control = SocialCalc.GetCurrentWorkBookControl();
    var html = control.workbook.spreadsheet.CreateSheetHTML();
    // this is for browser based print functionality
    var printWindow = window.open('','','left=100,top=100');
    printWindow.document.write(html);
    printWindow.print();
    printWindow.close();
}


function validateFileName(fname){
    if(fname.indexOf("\'") > 0 || fname.indexOf("\"") > 0)
        return false;
    else if(!fname)
        return false;
    else if(fname.length > 30)
        return false;
    else if(fname == "default" || fname == "Untitled")
        return false;
    return true;
}


function getFilePrefix(){
    return "/"+Global.app+"/";    
}

function getPathFromFilename(fname){

    var path = getFilePrefix()+fname;
    console.log("from fname "+fname+" path= "+path);
    return path;
}

function isFileForApp(filename){

    var prefix = getFilePrefix();

    if (filename.slice(0,prefix.length) == prefix) {
    return true;
    } 
    
    return false;
}

function getFilenameFromPath(path){

    var prefix =  getFilePrefix();

    if (path.slice(0,prefix.length) == prefix) {
    var fname = path.slice(prefix.length,path.length);
    console.log("from path = "+path+" name= "+fname)
    return fname;
    } 
    return null;

}

function saveAsOk(fname){
    //alert(name);
    var val = SocialCalc.WorkBookControlSaveSheet();
    console.log(val.length);
    var val1 = encodeURIComponent(val);
    console.log(val1.length);
  
    window.localStorage.setItem(getPathFromFilename(fname), val1);    
    //alert("Saved as "+fname);
    showAlert('Saved '+fname+' successfully ', 'Success!', 'alert alert-success');
     
     
    
}
 

function showAlert(message, tag, className){

    var alertElement = document.getElementById('alert-div');
    $('#alert-div').removeClass();
    $('#alert-div').addClass(className);
    alertElement.innerHTML = '<a class="close" onclick="closeAlert()">&times;</a><strong>'+tag+'</strong> '+message+'';
    alertElement.style.display = '';

    window.setTimeout(function(){
      alertElement.style.display = 'none';
    },4000);

}
 
function closeAlert(){
    var alertElement = document.getElementById('alert-div');
    alertElement.style.display = 'none';
}


function viewFile(filename){
    console.log("view file "+filename);
     
    if (filename != "default") {
       
    
      var fileData = window.localStorage.getItem(getPathFromFilename(filename));
      if (fileData) {
        SocialCalc.WorkBookControlInsertWorkbook(decodeURIComponent(fileData));
        updateFileName(filename);
        SocialCalc.GetCurrentWorkBookControl().workbook.spreadsheet.editor.state = "start";

        SocialCalc.GetCurrentWorkBookControl().workbook.spreadsheet.ExecuteCommand('redisplay', '');
        return true;

      
      } 
      else {
        showAlert('Error loading file '+filename+'', 'Error!', 'alert alert-danger'); 
        return false;
      }
    
    } else {
        data = document.getElementById("sheetdata").value;
        SocialCalc.WorkBookControlInsertWorkbook(data);
        updateFileName(filename);
        SocialCalc.GetCurrentWorkBookControl().workbook.spreadsheet.editor.state = "start";
        
        SocialCalc.GetCurrentWorkBookControl().workbook.spreadsheet.ExecuteCommand('redisplay', '');
        return true;
         
    }
 
}


function viewCloudFile(name, data){
    console.log("view file "+name);
    var fileData = decodeURIComponent(data);
    SocialCalc.WorkBookControlInsertWorkbook(fileData);
    updateFileName(name);
    SocialCalc.GetCurrentWorkBookControl().workbook.spreadsheet.editor.state = "start";
    SocialCalc.GetCurrentWorkBookControl().workbook.spreadsheet.ExecuteCommand('redisplay', '');
    return true;
}

function saveCurrentFile(){
    console.log("save file "+selectedFile);
    if (selectedFile == "default") {
       
       showAlert('Cannot update default file! Use SaveAs! ', 'Update!', 'alert alert-warning'); 
       return;
    }

    console.log("saving current file "+selectedFile)
    var val = SocialCalc.WorkBookControlSaveSheet();
    console.log(val.length);
    var val1 = encodeURIComponent(val);
    console.log(val1.length);
    window.localStorage.setItem(getPathFromFilename(selectedFile), val1);    
    console.log("saved as "+selectedFile);
    
    //alert("Saved file : "+selectedFile);
    showAlert('Updated '+selectedFile+' successfully ', 'Success!', 'alert alert-success');
}

function deleteFilePrompt(filename) {
  
  if (filename == "default" || filename == "Untitled") {
        //alert(filename);
        showAlert('Cannot delete '+filename+'', 'Delete!', 'alert alert-warning');  
        return false;
  }
 
  
  var confirm = window.confirm("Delete file: "+filename+" ?");  
  if(confirm){
    window.localStorage.removeItem(getPathFromFilename(filename));

    showAlert('Deleted file '+filename+' successfully', 'Success!', 'alert alert-success');
 
    return true;
  }
  return false;
}



function validateEmail(email){
    var re = /^(([^<>()[\]\\.,;:\s@"]+(\.[^<>()[\]\\.,;:\s@"]+)*)|(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}])|(([a-zA-Z\-0-9]+\.)+[a-zA-Z]{2,}))$/;
    return re.test(email);
}

function getUserInfo(){
    if(!window.sessionStorage.getItem("userLogin")) {
        return null;
    }
    else{
        return window.sessionStorage.getItem("userLogin");
    }
    
}

function request(message){
    
    return  $.ajax({ type: message.type, url: message.url, data: message.data, dataType: message.format});
}
 