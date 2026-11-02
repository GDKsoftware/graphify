unit WithBlok;

interface

uses Main;

type
  TFoo = class
  public
    procedure Bar;
  end;

procedure Bar;
procedure Test;
procedure Lokaal(frmMain: TObject);

implementation

procedure TFoo.Bar;
begin
end;

procedure Bar;
begin
end;

procedure Test;
begin
  with TFoo.Create do Bar;
  with GetObj do Bar;
  with frmMain do begin DoeIets; end;
  with A, B do Bar;
  with TFoo.Create do with GetObj do Bar;
end;

procedure Lokaal(frmMain: TObject);
begin
  with frmMain do begin DoeIets; end;
end;

end.
