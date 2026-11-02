unit FormB;

interface

uses Basis, Andere;

type
  TfrmB = class(TfrmBasis)
  public
    procedure Doe;
  end;

implementation

procedure TfrmB.Doe;
begin
  Verander;
  Geheim;
end;

end.
