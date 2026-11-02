unit Main;

interface

type
  TfrmMain = class(TForm)
  private
    procedure Verborgen;
  public
    procedure DoeIets;
  end;

  THelper = class
  public
    procedure Help;
    class function Maak: THelper;
  end;

var
  frmMain: TfrmMain;

implementation

procedure TfrmMain.Verborgen;
begin
end;

procedure TfrmMain.DoeIets;
begin
  Verborgen;
end;

procedure THelper.Help;
begin
end;

class function THelper.Maak: THelper;
begin
  Result := nil;
end;

end.
