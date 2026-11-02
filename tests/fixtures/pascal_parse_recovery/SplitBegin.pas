unit SplitBegin;

interface

type
  TWerk = class
  public
    procedure Stap;
    procedure Start;
  end;

procedure Los;

implementation

procedure Los;
begin
end;

procedure TWerk.Stap;
begin
  Los;
end;

procedure TWerk.Start;
begin
{$IFDEF EEN}
  if True then begin
{$ELSE}
  if False then begin
{$ENDIF}
    Stap;
  end;
end;

end.
