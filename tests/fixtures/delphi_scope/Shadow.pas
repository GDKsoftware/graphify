unit Shadow;

interface

uses Vcl.Forms, ShadowLib;

type
  TFoo = class
  end;

  TNotifyEvent = procedure(Sender: TObject) of object;

  TfrmS = class(TForm)
  private
    FOnChange: TNotifyEvent;
  public
    procedure Cast(X: TObject);
    procedure Event;
    procedure Sluit;
  end;

implementation

procedure TfrmS.Cast(X: TObject);
var
  Y: TObject;
begin
  Y := TFoo(X);
end;

procedure TfrmS.Event;
begin
  FOnChange(Self);
end;

procedure TfrmS.Sluit;
begin
  Close;
end;

end.
