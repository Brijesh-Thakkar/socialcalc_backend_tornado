var app = angular.module('aspiringapps', ['ngRoute', 'ui.bootstrap', 'ngSanitize', 'ngResource']);
  
app.config(['$routeProvider', function($routeProvider) {
        $routeProvider.
        
        when('/listFiles', {
           templateUrl: 'listFiles.html',
           controller: 'ListFilesCtrl'
        }).
         
        when('/home', {
           templateUrl: 'home.html',
           controller: 'HomeCtrl'
        }).

        when('/login', {
           templateUrl: 'login.html',
           controller: 'LoginCtrl'
        }).

        when('/register', {
           templateUrl: 'registration.html',
           controller: 'RegisterCtrl'
        }).

        when('/spreadsheet/:appName', {
           templateUrl: 'spreadsheet.html',
           controller: 'WebAppCtrl'
        }).

        when('/listCloudFiles', {
           templateUrl: 'listCloudFiles.html',
           controller: 'ListCloudFilesCtrl'
        }).

        otherwise({
           redirectTo: '/spreadsheet/'+Global.app
        });
}]);

app.run(function($rootScope, $location){

    $rootScope.switchToHome = function() {
        $location.path("/home");
    };

    $rootScope.back = function(){
      window.history.back();
    };

    $rootScope.backToList = function(){
      window.open("/web/portal","_self");  
    };

    $rootScope.webAppName = Global.app;



});


app.controller('HomeCtrl', function($scope, AppService, $location, $uibModal, CloudService) {

	$scope.apps = new Array();
    var params = {
        'action':'list'
    };

    AppService.getData(params).then(function(response){
    	
    	var result = response.data.data;
    	//$scope.apps = result;
    	//console.log(JSON.stringify(result));
    	for(var i in result){
            var appDir = result[i];
            var ind = appDir.indexOf(" ");
            while (ind != -1) {
                appDir = appDir.replace(" ","");
                ind = appDir.indexOf(" ");
            }
            //console.log(appDir);

    		var icon = "/web/static/apps/"+appDir+"/icon.png"
    		$scope.apps.push({"name":result[i], "src":icon});
    	}
    	//console.log("apps list: "+JSON.stringify($scope.apps));

    });

    $scope.loadWebApp = function(name){
        var ind = name.indexOf(" ");
            while (ind != -1) {
                name = name.replace(" ","");
                ind = name.indexOf(" ");
        }

        $location.path("spreadsheet/"+name);
    };

    var email = window.sessionStorage.getItem("userLogin");
    $scope.showLogin = false ;  $scope.showLogout = true;
    if(email){
      $scope.showLogin = false; $scope.showLogout = true;
    }
    else{
      $scope.showLogin = true; $scope.showLogout = false;
    }
    $scope.user = {
      templateUrl: 'loginPopoverTemplate.html',
      title: email,
      logIn: $scope.showLogin,
      logOut: $scope.showLogout
    };


    $scope.startLogin = function(){
       
       CloudService.getSession().then(function(response){
        
          var result = response.result;
          if(result == true){
             // Logged in
             console.log("Session exists!");
             $scope.showLogin = false;
             $scope.showLogout = true;

             $scope.user = {
                templateUrl: 'loginPopoverTemplate.html',
                title: email,
                logIn: $scope.showLogin,
                logOut: $scope.showLogout
            };
          }
          else{
             $scope.openLoginModal('lg');
          }
       });
    };

    $scope.$on("loginComplete", function (event, args) {
      console.log("Login complete");
      $scope.showLogin = false;
      $scope.showLogout = true;
      $scope.user = {
          templateUrl: 'loginPopoverTemplate.html',
          title: args.user,
          logIn: $scope.showLogin,
          logOut: $scope.showLogout
       };

    });


    $scope.startLogout = function(){
       CloudService.startLogout().then(function(response){
          var result = response.data.result;
          if(result == "ok"){
             showAlert('Log out completed', 'Success!', 'alert alert-success');
             window.sessionStorage.removeItem("userLogin");

             $scope.showLogin = true;
             $scope.showLogout = false;
             $scope.user = {
                templateUrl: 'loginPopoverTemplate.html',
                title: "",
                logIn: $scope.showLogin,
                logOut: $scope.showLogout
            };
          }
       });

    };


    $scope.openLoginModal = function(size){

        var modalInstance = $uibModal.open({
          animation: true,
          templateUrl: 'login.html',
          controller: 'LoginCtrl',
          size: size,
          backdrop: 'static', /*  this prevent user interaction with the background  */
          keyboard: false

        });

      modalInstance.result.then(function (selectedItem) {
          $scope.selected = selectedItem;
        }, function () {
          console.log('Modal dismissed at: ' + new Date());
      });

    };
});

app.controller('WebAppCtrl', function($scope, $routeParams, AppService, $uibModal, FileService, $location, CloudService, $timeout, SaveService){
    
    $scope.loader = {'load': false };
    var name = Global.app;
    //console.log(name);
    $scope.currentFile = selectedFile;
    //$scope.webAppName = name;

    var params = {
        'action':'get-msc-data', 'appname':name
    };

    if(selectedFile == "default"){
      AppService.getData(params).then(function(response){
        var result = response.data.data;
        //console.log(decodeURIComponent(result.data));
        //console.log("Loading file.."+selectedFile);

        $scope.msc = result.data;
        loadAndStartUpApp($scope.msc);

      });
    }
    else{
      var fileData = window.localStorage.getItem(getPathFromFilename(selectedFile));
      if(!fileData){
        $scope.msc = '';
      }
      else{
        $scope.msc = decodeURIComponent(fileData);
        loadAndStartUpApp($scope.msc);
      }
      
    }

    if($scope.msc == ''){
      var user = getUserInfo();

      var params = { 'action': 'get-file-data', 'filename': selectedFile, 'appname': Global.app, "user": user};
      $scope.loader.load = true;  
      CloudService.getFileData(params).then(function(response){
          $scope.loader.load = false;  
          var result = response.data.result;
          if(result == "ok"){
            var data = response.data.data;
            $scope.msc = decodeURIComponent(data);
            
            loadAndStartUpApp($scope.msc);
          }
      
       });
    }

    $scope.footers = new Array(); $scope.params ='';
    $scope.progress = {bar: false, percent: 0, message: "Uploading"};

    var footers = new Array();
    var match = ["u","[","]","'","'"];
    var str = Global.footers.split(",");
    for( i in str){
         for(var j=0;j<match.length;j++){
          var ind = str[i].indexOf(match[j]);
          str[i] = str[i].replace(match[j],'');
         }
        footers.push(str[i]);
    }

    for(var i in footers){
        var index = parseInt(i)+parseInt(1);
        $scope.footers.push({"name": footers[i], "index": index});
    }


    
    // Store the session object
    if(!window.sessionStorage.getItem("userLogin")){
      CloudService.setSession().then(function(response){
         //alert(JSON.stringify(response));
         var result = response.result;
         if(result == "ok"){
            console.log("Session set");
            
         }
      });
    }




    /*AppService.getData(params).then(function(response){
        var result = response.data.data;
        //console.log(JSON.stringify(result));
        console.log("Loading file.."+selectedFile);
        if(selectedFile == "default"){
            $scope.msc = result.data;
        }
        else{
            var fileData = window.localStorage.getItem(getPathFromFilename(selectedFile));
            if(!fileData){
              $scope.msc = '';
            }
            else{
              $scope.msc = decodeURIComponent(fileData);
            }
        }
        var footer = result.footer;
        for(var i in footer){
            var index = parseInt(i)+parseInt(1);
            $scope.footers.push({"name": footer[i], "index": index});
        }

        //Load and start up called every time can be optimised
        if($scope.msc == ''){
          var user = getUserInfo();

          var params = { 'action': 'get-file-data', 'filename': selectedFile, 'appname': Global.app, "user": user};
          $scope.loader.load = true;  
          CloudService.getFileData(params).then(function(response){
              $scope.loader.load = false;  
              var result = response.data.result;
              if(result == "ok"){
                var data = response.data.data;
                $scope.msc = decodeURIComponent(data);
                // console.log("received: "+$scope.msc);
                loadAndStartUpApp($scope.msc);
              }
          
           });
        }
        else{
          loadAndStartUpApp($scope.msc);
        }*/

        
   // });


    $scope.activateFooterButton = function(index){
        //alert(index);
        activateFooterButton(index);
    };

    $scope.print = function(){
        showPrintDialog();
    };

    $scope.email = function(size){

        var emailModalInstance = $uibModal.open({
          animation: true ,
          templateUrl: 'emailModal.html',
          controller: 'EmailCtrl',
          size: size,
          backdrop: 'static', /*  this prevent user interaction with the background  */
          keyboard: false

        });

        emailModalInstance.result.then(function (selectedItem) {
              $scope.selected = selectedItem;
        }, function () {
              console.log('Modal dismissed at: ' + new Date());
        });


    };

    $scope.export = function(size){

      var exportModalInstance = $uibModal.open({
          animation: true ,
          templateUrl: 'exportModal.html',
          controller: 'ExportCtrl',
          size: size,
          backdrop: 'static', /*  this prevent user interaction with the background  */
          keyboard: false

        });

        exportModalInstance.result.then(function (selectedItem) {
              $scope.selected = selectedItem;
        }, function () {
              console.log('Modal dismissed at: ' + new Date());
        });
    }

    $scope.saveAs = function(){
        var fname = prompt("Enter File Name","Saved");
        if(!fname){
          return;
        }    
        if (validateFileName(fname)) {
            //alert("Validate");
            FileService.findByName(Global.app).then(function(response){
                //alert(JSON.stringify(response));
                for(var i in response){
                    if(response[i].toLowerCase() == fname.toLowerCase()){
                        alert("File with the same name already exists!");
                        return;
                    }

                } 
                saveAsOk(fname);
            });

        }
        else{
            alert("Invalid file name");
        }
    };

    $scope.list = function(){
        $location.path("/listFiles");
    };

    $scope.save = function(){
        saveCurrentFile();
    };


    $scope.cloudSave = function(){

        var val = encodeURIComponent(SocialCalc.WorkBookControlSaveSheet());
        console.log(val.length);
        var user = getUserInfo();

        var filename = prompt("Enter File Name","Saved");
        if(!filename){
          return;
        }

        if (validateFileName(filename)) {

          CloudService.getSession().then(function(response){

            var result = response.result;
            if(result == true){
              
              console.log("Session exists! Continue saving...");
              var params = {'action': 'savefile', 'fname':filename , 'data': val, 'appname': Global.app};
              $scope.params = params;

              $scope.openProgressModal();

              var message ={
                url: '/web/webapp', type: 'POST', format: 'json', data: params
              };

              request(message).done(function(response){
                console.log(JSON.stringify(response));
                var result = response.result;
                if(result == "ok"){
                    $scope.$root.$broadcast("saveCompleted", {result: "ok" });
                    showAlert('Save completed', 'Success!', 'alert alert-success');

                    //Update Save Counter
                    var data ={
                      url: '/web/webapp', type: 'POST', format: 'json', data: {'action':'update', 'appname': Global.app}
                    };
                    request(data).done(function(response){
                       var result = response["result"]
                       console.log("update: "+result);
                    });
                    
                }
                else if(result == "buy"){
                    // alert("Please purchase from iPad to continue");
                    $scope.$root.$broadcast("inappRequired", {result: "fail", message: "Please purchase from iPad to continue!" });
                } 
                else{
                    $scope.$root.$broadcast("saveFailed", {result: "fail", message: "Save file failed. Try again!" });
                }
              });
            
             
            }
            else{
              alert("Login required!");
            }

          }); // getSession ends
        }
        else{
            alert("Invalid file name");
        }

    };

   
    $scope.openProgressModal = function(){

        var modalInstance = $uibModal.open({
          animation: true,
          templateUrl: 'progressModal.html',
          controller: 'progressModalCtrl',
          size: 'lg',
          resolve: {
            params: function () {
              return $scope.params;
            }
          },
          backdrop: 'static', /*  this prevent user interaction with the background  */
          keyboard: false

        });
 

    };


    $scope.cloudUpdate = function(){
      var val = encodeURIComponent(SocialCalc.WorkBookControlSaveSheet());
      console.log(val.length);
      var user = getUserInfo();
      if(selectedFile == "default"){
        alert("Cannot update default file");
        return;
      }

      CloudService.getSession().then(function(response){
        var result = response.result;
            if(result == true){

              console.log("Session exists!");
              var params = {'action': 'savecurrentfile', 'fname':selectedFile , 'data': val, 'appname': Global.app, "user": user};
              $scope.params = params;
              $scope.openProgressModal();

              var message ={
                url: '/web/webapp', type: 'POST', format: 'json', data: params
              };

              request(message).done(function(response){
                console.log(JSON.stringify(response));
                var result = response.result;
                if(result == "ok"){
                    $scope.$root.$broadcast("updateCompleted", {result: "ok" });
                    showAlert('Update completed', 'Success!', 'alert alert-success');
                }
                else{
                    //console.log(result);
                    $scope.$root.$broadcast("updateFailed", {result: "fail", message: "File does not exists" });
                }
              });
 
            }
            else{
              alert("Login required!");
            }
      });
    };

    $scope.listCloudFiles = function(){
       CloudService.getSession().then(function(response){
          var result = response.result;
          if(result == true){
            console.log("Session exists! Continue listing files");
            $location.path("/listCloudFiles");
          }
          else{
            alert("Login required");
          }

       });

    };

    //Progress bar
   




});

app.controller('ListFilesCtrl', function($scope, FileService, $location, CloudService){

    $scope.display = {  'list': true };
    $scope.spinner = {  'load': false };

    FileService.all().then(function(files){
        $scope.files = files;
    });

    $scope.backToWebapp = function(){
        $location.path("spreadsheet/"+Global.app);
    };


    $scope.editFile = function(name){
      if(name == "default"){
        selectedFile = "default";
        $location.path("spreadsheet/"+Global.app);
      }
      else{
        var bool = viewFile(name);
        if(bool){
            selectedFile = name;
            $location.path("spreadsheet/"+Global.app); 
        }
      }

    };

    $scope.deleteFile = function(name){
      var is_deleted = FileService.delete(name);
      if(is_deleted){
        FileService.all().then(function(files){
            $scope.files = files;
        });
      }
    };

   
    $scope.uploadToServer = function(){
        var oldList = $scope.files;
        var fileNames = [];

        angular.forEach(oldList, function(x) {
               if(x.checked == false || x.checked == "false"){
                        
               }
               else{
                 fileNames.push(x.name);
               }
        });
        console.log("checked files: "+JSON.stringify(fileNames));
        
        $scope.filesData = {};
        if(fileNames != ''){
          
          for(var i in fileNames){
            $scope.filesData[fileNames[i]] = encodeURIComponent(window.localStorage.getItem(getPathFromFilename(fileNames[i])));
            //console.log(JSON.stringify(filesData));
          }
          $scope.filesData = JSON.stringify($scope.filesData);
          var user = getUserInfo();
          var params = { 'action': 'save-multiple', 'appname': Global.app, "user": user, "content": $scope.filesData};

          var message ={
                url: '/web/webapp', type: 'POST', format: 'json', data: params
          };

          
          CloudService.getSession().then(function(response){
            var result = response.result;
            if(result == true){
              $scope.spinner.load = true;
              console.log("Session exists! Continue save-multiple");

              request(message).done(function(response){ 
                //console.log(JSON.stringify(response));
                var result = response.result;
                $scope.spinner.load = false;
                
                if(result == "ok"){ 
                    showAlert('Files uploaded successfully ', 'Success!', 'alert alert-success');
                }
                else{
                    showAlert('Files not uploaded. Try again! ', 'Failure!', 'alert alert-danger');
                }
              });




            }
            else{
              $scope.spinner.load = false;
              alert("Login required");
            }
          }); // getSession ends

        }
        else{
          alert("No file selected");
        }

    };


});

app.controller('ListCloudFilesCtrl', function($scope, $location, CloudService){

    $scope.display = {  'list': true };
    $scope.spinner = {  'load': false };
    $scope.loader = {'load': false };
    
    var user = getUserInfo();
    var params = {'action': 'listdir', 'appname': Global.app , "user": user };

    $scope.loader.load = true;
    //document.body.style.opacity = "0.5";
    CloudService.listAll(params).then(function(files){
        $scope.files = files;
        $scope.loader.load = false;
        //document.body.style.opacity = "1";
        // alert(JSON.stringify($scope.files));
    });

    $scope.editFile = function(name){
      selectedFile = name;
      $location.path("spreadsheet/"+Global.app); 
    };

    $scope.deleteFile = function(name){
      var confirm = window.confirm("Delete file: "+name+" ?");  
      if(confirm){

        CloudService.getSession().then(function(response){
            var result = response.result;
            if(result == true){
              console.log("Session exists!Continue deleting");
              var params = { 'action': 'delete', 'fname': name, 'appname': Global.app, "user": user};
              CloudService.deleteCloudFile(params).then(function(response){
                  var user = getUserInfo();
                  var params = {'action': 'listdir', 'appname': Global.app , "user": user };

                  CloudService.listAll(params).then(function(files){
                    $scope.files = files;
                    // alert(JSON.stringify($scope.files));
                  });
              });
            }
            else{
              alert("Login required");
            }
        });
      }
      else{
        return;
      }
    };

    $scope.deleteFromServer = function(){
        var oldList = $scope.files;
        var fileNames = [];

        angular.forEach(oldList, function(x) {
               if(x.checked == false || x.checked == "false"){
                        
               }
               else{
                 fileNames.push(x.name);
               }
        });
        console.log("checked files: "+JSON.stringify(fileNames));
        $scope.spinner.load = true;
        var user = getUserInfo();
        var params = { 'action': 'delete-multiple', 'appname': Global.app, 'data': JSON.stringify(fileNames), "user": user };
        CloudService.getSession().then(function(response){
            var result = response.result;
            if(result == true){
              console.log("Session exists!");
              CloudService.deleteCloudFile(params).then(function(response){
                //alert(JSON.stringify(response));
                $scope.spinner.load = false;
                var result = response.data.result;
                  if(result == "ok"){
                     showAlert('Files deleted successfully ', 'Success!', 'alert alert-success');
                     var params = {'action': 'listdir', 'appname': Global.app , "user": user };

                      CloudService.listAll(params).then(function(files){
                        $scope.files = files;
                        // alert(JSON.stringify($scope.files));
                      });
                  }
              });
            }
            else{
              $scope.spinner.load = false;
            }
          }); // getSession ends

    };



});


app.controller('EmailCtrl', function($scope, $uibModalInstance, EmailService, CloudService){

    $scope.spinner = { 'load': false };

    $scope.cancel = function () {
        $uibModalInstance.dismiss('cancel');
    };

    $scope.user = {}; var message = {};
    var control = SocialCalc.GetCurrentWorkBookControl();
    var content = control.workbook.spreadsheet.CreateSheetHTML();
    //console.log(content);

    $scope.doSend = function(){

        message.appname = Global.app;
        message.to = $scope.user.emailTo;
        message.data = encodeURIComponent(content);
        message.subject = $scope.user.subject;
        message.text = $scope.user.message;
        message.user = getUserInfo();

        if(!message.to){
          alert("No recipient found!");
          return;
        }
        //console.log(JSON.stringify(message));
        $scope.spinner.load = true;
        CloudService.getSession().then(function(response){
            var result = response.result;
            if(result == true){
              console.log("Session exists! Continue email");
              EmailService.send(message).then(function(response){
                $scope.spinner.load = false;
                $scope.cancel();
                var result = response.result;
                if(result == "ok"){
                  showAlert('Email sent', 'Success!', 'alert alert-success');
                }
              });
            }
            else{
              alert("Login required!");
            }
            
        });
        

    };

    $scope.emailContent = content;

    

});

app.controller('progressModalCtrl', function ($scope, $uibModal, $uibModalInstance, $location, CloudService, params) {

   
   $scope.spinner = { 'load': true, 'done': false, 'fail': false};
   $scope.params = params;
   // console.log("The file is " + JSON.stringify($scope.params));
   if($scope.params.action == "savefile"){
    $scope.title = "Saving file "+$scope.params.fname+" to server";
   }
   else{
    $scope.title = "Updating file "+$scope.params.fname+" to server";
   }

   $scope.substring = "Sending data";
    
   $scope.$on("saveCompleted", function (event, args) {
     
     if(args.result == "ok"){
      console.log("Saved");
      $scope.substring = "Save successful";
      $scope.spinner.done = true;
      $scope.spinner.load = false;
      window.setTimeout(function(){ $scope.cancel();  }, 1300);
     }
     
  });

 

  $scope.$on("updateCompleted", function (event, args) {
     
     if(args.result == "ok"){
      console.log("updateCompleted");
      $scope.substring = "Update successful";
      $scope.spinner.done = true;
      $scope.spinner.load = false;
      window.setTimeout(function(){ $scope.cancel();  }, 1300);
     // $scope.cancel();
     }
     
  });

  $scope.$on("updateFailed", function (event, args) {
    console.log("File updating failed")
    alert(args.message);
    $scope.substring = args.message;
    $scope.spinner.fail = true;
    $scope.spinner.load = false;
    window.setTimeout(function(){ $scope.cancel();  }, 1200);

  });

  $scope.$on("saveFailed", function (event, args) {
    alert(args.message);
    $scope.substring = args.message;
    $scope.spinner.fail = true;
    $scope.spinner.load = false;
    window.setTimeout(function(){ $scope.cancel();  }, 1200);

  });

  $scope.$on("inappRequired", function (event, args) {
    alert(args.message);
    $scope.substring = args.message;
    $scope.spinner.fail = true;
    $scope.spinner.load = false;
    window.setTimeout(function(){ $scope.cancel();  }, 1400);

  });

   $scope.cancel = function () {
    $uibModalInstance.dismiss('cancel');
   };
 
    

});


app.controller('LoginCtrl', function ($scope, $uibModal, $uibModalInstance, $location, CloudService) {

  $scope.user = {};
  $scope.spinner = {load : false};
  $scope.incorrectPassword = false;

  $scope.login = function () {
     $scope.incorrectPassword = false;
     var email = $scope.user.email;
     var password = $scope.user.password;
     if(validateEmail(email)){
      var params = {'action':'login', 'user':email, 'password':password };
      $scope.spinner.load = true;
       CloudService.startLogin(params).then(function(response){
          // alert(JSON.stringify(response));
          $scope.spinner.load = false;
          var result = response.data.result;
          if(result == "ok"){
            showAlert('Login successful ', 'Success!', 'alert alert-success');
            $scope.cancel();
            $scope.$root.$broadcast("loginComplete", {"user": $scope.user.email });
            if(!window.sessionStorage.getItem("userLogin")){
              CloudService.setSession().then(function(response){
                 //alert(JSON.stringify(response));
                 var result = response.result;
                 if(result == "ok"){
                    console.log("Session set");
                    
                 }
              });
            }
          }
          else if(result == "fail"){
            $scope.openRegistrationModal();
          }
          else if(result == "wrong"){
            $scope.incorrectPassword = true;
            $scope.errorMessage = "Authentication failed. Password does not match. Try again";
          }
       });
     }
      

  };

  $scope.cancel = function () {
    $uibModalInstance.dismiss('cancel');
  };

  $scope.openRegistrationModal = function(){
    $scope.cancel();

    var modalInstance = $uibModal.open({
          animation: true,
          templateUrl: 'registration.html',
          controller: 'RegisterCtrl',
          size: 'lg',
          backdrop: 'static', /*  this prevent user interaction with the background  */
          keyboard: false

        });

      modalInstance.result.then(function (selectedItem) {
          $scope.selected = selectedItem;
        }, function () {
          console.log('Modal dismissed at: ' + new Date());
      });


  };

});


app.controller('RegisterCtrl' , function ($scope, $uibModal, $uibModalInstance, $location, CloudService) {

 $scope.spinner = {load : false};
  var register = {};
  $scope.register = function () {
     var email = $scope.register.email;
     var password = $scope.register.password;
     if(validateEmail(email)){
      var params = {'action':'register', 'user':email, 'password':password };
      $scope.spinner.load = true;
       CloudService.startRegister(params).then(function(response){
          //alert(JSON.stringify(response));
          $scope.spinner.load = false;
          var result = response.data.result;
          if(result == "ok"){
            showAlert('Registration successful ', 'Success!', 'alert alert-success');
            $scope.cancel();
          }
          else if(result == "exists"){
            alert("User exists!");
            $scope.openLoginModal();
          }
       });
     }
  };

  $scope.cancel = function () {
    $uibModalInstance.dismiss('cancel');
  };

  $scope.openLoginModal = function(){
   $scope.cancel();

    var modalInstance = $uibModal.open({
          animation: true,
          templateUrl: 'login.html',
          controller: 'LoginCtrl',
          size: 'lg',
          backdrop: 'static', /*  this prevent user interaction with the background  */
          keyboard: false

        });

      modalInstance.result.then(function (selectedItem) {
          $scope.selected = selectedItem;
        }, function () {
          console.log('Modal dismissed at: ' + new Date());
      });

  };

});


app.controller('ExportCtrl', function($scope, $uibModalInstance, CsvService){

  $scope.cancel = function () {
        $uibModalInstance.dismiss('cancel');
  };

  $scope.file = { name: 'Untitled'}; 
  $scope.showspan = false;
  
  $scope.doExport = function(){
      $scope.showspan = false;
      CsvService.getCsv().then(function(response){
          // alert(response);
          
          var exportLink = document.createElement('a');
          exportLink.setAttribute('href', 'data:text/csv;base64,' + window.btoa(response));

          var name =  $scope.file.name;
          exportLink.setAttribute('download', name);
          // alert(name);
          var txtNode = document.createTextNode(name+'.csv');
          exportLink.appendChild(txtNode);
          var el = document.getElementById('results');
          while( el.hasChildNodes() ){
            el.removeChild(el.lastChild);
          }
          document.getElementById('results').appendChild(exportLink);
          $scope.showspan = true;

      });
      
  };

});

app.factory('CsvService', function($q){
  return{
    getCsv: function(){
      var deferred = $q.defer();
      var val = SocialCalc.WorkBookControlSaveSheet();
      // alert(val);
      var workBookObject = JSON.parse(val);
      // alert(workBookObject);
      var control = SocialCalc.GetCurrentWorkBookControl();
      currentname = control.currentSheetButton.id; //predefined variable.replace id by name for fixed sheet.
      //alert(currentname);
      var savestrr = workBookObject.sheetArr[currentname].sheetstr.savestr;
      var res = SocialCalc.ConvertSaveToOtherFormat(savestrr, "csv", false);
      
      if(res){
        deferred.resolve(res);
      }
      else{
        deferred.reject("Error");
      }
      return deferred.promise;

    }
  };
});

app.factory('SaveService', function($resource){

  return $resource('/cloudStore/', {
    action: "@action", filename: "@filename", content: "@content", appname: "@appname", user: "@user"
    },{
    save: {
      method: 'POST'
    }

    });

});
app.factory('AppService', function($http, $q){

	return{
		getData:function(params){
      return $http({ url:"/web/webapp",method:"POST",params:params});
		}
	};
});

app.factory('EmailService', function(){
    return{
        send: function(params){
            //return $http({ url:"/emailer",method:"POST",params:params});
            return $.ajax({ type: "POST", url: "/web/runasemailer", data: params, dataType: "json"});
        }

    };
});

app.factory('FileService', function($http, $q){
    return{
        findByName: function(name){
             var files = new Array();
             var deferred = $q.defer();
             
             for (i=0; i < window.localStorage.length; i++) {
                if (!(isFileForApp(window.localStorage.key(i)))) { 
                  continue;
                }
                if(window.localStorage.key(i).length >=30)
                  continue;
                var filename = getFilenameFromPath(window.localStorage.key(i));
                files.push(filename);
             
             }
             files.push("default");
            
             var results = files.filter(function(element) {
                var fullName = element;
                 
                return fullName;
             });
             deferred.resolve(results);
             return deferred.promise;
        },
        delete:function(file){
          var status = deleteFilePrompt(file);
          return status;
        },
        all: function(){
              var files = new Array();
              var deferred = $q.defer();


              for (i=0; i < window.localStorage.length; i++) {

                if (!(isFileForApp(window.localStorage.key(i)))) { 
                  continue;
                }

                if(window.localStorage.key(i).length >=30)
                  continue;

                var fname = getFilenameFromPath(window.localStorage.key(i));
                fileobj = JSON.parse(decodeURIComponent(window.localStorage.getItem(getPathFromFilename(fname))));
                var date = new Date(fileobj['timestamp']);
                var timestamp = date.toLocaleString();
                files.push({'name':fname, 'timestamp': timestamp, "checked": false});

              }

              var date = new Date();
              var timestamp = date.toLocaleString();
              files.push({'name':'default', 'timestamp': timestamp, "checked": false});

              var results = files.filter(function(element) {
                                          return element;
                                        });
             deferred.resolve(results);
             return deferred.promise;
         }
    };
});

app.factory('CloudService', function($http, $q){
return{

getSession: function(){

  var deferred = $q.defer();
  
  if(window.sessionStorage.getItem("userLogin")){
    deferred.resolve({"result": true});
  }
  else{
    $http.get("/web/authenticate",{params:{action:"get-login"}}).
    then(function(response){
        var result = response.data.result;
        if(result == "ok"){
            var data = response.data.data;
            window.sessionStorage.setItem('userLogin', data);
            deferred.resolve({"result": true});
        }
        else{
            deferred.resolve({"result": false}); 
        }
    }, function(error){
      deferred.resolve({"result": false}); 
    });
  }

  return deferred.promise;

},
setSession: function(){
  var deferred = $q.defer();
  $http.get("/web/authenticate",{params:{action:"get-login"}}).
    then(function(response){
        var result = response.data.result;
        if(result == "ok"){
            var data = response.data.data;
            window.sessionStorage.setItem("userLogin", data);
            deferred.resolve({"result": true});
        }
        else{
            deferred.resolve({"result": false}); 
        }
    }, function(error){
      deferred.resolve({"result": false}); 
  });

  return deferred.promise;
},

startLogin: function(params){
  return $http({ url:"/auth",method:"POST",params:params});

},
startRegister: function(params){
  return $http({ url:"/auth",method:"POST",params:params});
},
save: function(params){
  return $http({ url:"/cloudStore",method:"POST",params:params});
},
startLogout: function(){
  return $http({ url:"/auth",method:"POST",params:{'action':'logout'}});

},
listAll: function(params){
  var deferred = $q.defer();
  var files = new Array();
  $http({ url:"/web/webapp",method:"POST",params:params}).
    then(function(response){
      var result = response.data.result;
      if(result == "ok"){
        var data = response.data.data;
        for(var i in data){
          // console.log(data[i])
          files.push({"name": data[i] , "checked": false});
        }
        deferred.resolve(files);
      }
  });
  
  return deferred.promise;
},
getFileData: function(params){
  return $http({ url:"/web/webapp",method:"POST",params:params});
},
deleteCloudFile: function(params){
  return $http({ url:"/web/webapp",method:"POST",params:params});
}

};
});