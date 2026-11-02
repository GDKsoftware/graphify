unit DataMod;

interface

uses Helpers, Main;

type
  TdmData = class(TDataModule)
  private
    FHelper: THelper;
  public
    procedure Laad;
    procedure Prive;
  end;

implementation

procedure TdmData.Laad;
var
  S: string;
begin
  Helpers.InitCap(X);
  S := 'select initcap(a) from dual';
  frmMain.DoeIets;
  THelper.Maak;
  FHelper.Help;
  Self.Laad;
  GetObj.Bar;
end;

procedure TdmData.Prive;
begin
  frmMain.Verborgen;
end;

end.
