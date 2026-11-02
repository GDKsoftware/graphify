unit Vcl.Dialogs;

interface

uses
  System;

type
  TDialog = class(TObject)
  protected
    procedure DoShow; virtual;
  public
    function Execute: Boolean;
  end;

function ShowMessage(const Msg: string): Integer;

implementation

uses
  System.Hidden;

function ShowMessage(const Msg: string): Integer;
begin
  Result := 0;
end;

function TDialog.Execute: Boolean;
begin
  Result := True;
end;

end.
