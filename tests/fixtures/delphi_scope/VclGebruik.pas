unit VclGebruik;

interface

uses Forms, Dialogs;

type
  TfrmV = class(TForm)
  public
    procedure Doe;
    procedure Wacht;
  end;

implementation

procedure TfrmV.Doe;
begin
  ShowMessage('x');
  Close;
  Application.ProcessMessages;
  MessageDlg('x', mtInfo, [], 0);
  IntToStr(1);
  UpperCase('x');
end;

procedure TfrmV.Wacht;
begin
  Application.ProcessMessages;
end;

end.
