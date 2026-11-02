unit Casts;

interface

uses System.Classes, Vcl.Forms;

type
  TfrmC = class(TForm)
  private
    FOnChange: TNotifyEvent;
  public
    procedure Klik(Sender: TObject);
  end;

implementation

procedure TfrmC.Klik(Sender: TObject);
begin
  TfrmC(Sender).Close;
  Close;
  if TObject(Sender) <> nil then
    FOnChange(Self);
end;

end.
