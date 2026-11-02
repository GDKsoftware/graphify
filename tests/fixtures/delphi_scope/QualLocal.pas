unit QualLocal;

interface

uses Main;

type
  TQual = class
  private
    frmMain: THelper;
  public
    procedure Param(frmMain: TObject);
    procedure Lokaal;
    procedure Veld;
  end;

implementation

procedure TQual.Param(frmMain: TObject);
begin
  frmMain.DoeIets;
end;

procedure TQual.Lokaal;
var
  THelper: TObject;
begin
  THelper.Maak;
end;

procedure TQual.Veld;
begin
  frmMain.DoeIets;
  frmMain.Help;
end;

procedure ParamVrij(frmMain: TObject);
begin
  frmMain.DoeIets;
end;

end.
